from app.models.paper import FullTextCandidate, PaperMetadata
from app.services.dedup import deduplicate
from app.services.ranking import rank_papers


def test_dedup_merges_doi_and_metadata():
    papers = [
        PaperMetadata(
            paper_id="a", title="KRAS G12C resistance", doi="10.1/ABC",
            source_hits=["openalex"], citation_count=10
        ),
        PaperMetadata(
            paper_id="b", title="KRAS G12C resistance", doi="https://doi.org/10.1/abc",
            abstract="Long abstract", pmid="123", source_hits=["europepmc"],
            full_text_candidates=[FullTextCandidate(source="europepmc", format="xml", priority=1)]
        ),
    ]
    result = deduplicate(papers)
    assert len(result) == 1
    assert result[0].pmid == "123"
    assert result[0].has_full_text
    assert set(result[0].source_hits) == {"openalex", "europepmc"}


def test_ranking_prefers_relevant_full_text():
    relevant = PaperMetadata(
        paper_id="a", title="KRAS G12C sotorasib resistance",
        year=2025, source_hits=["europepmc"], has_full_text=True
    )
    unrelated = PaperMetadata(
        paper_id="b", title="Unrelated historical review",
        year=1990, source_hits=["openalex"], citation_count=1000
    )
    ranked = rank_papers("KRAS G12C sotorasib resistance", [unrelated, relevant])
    assert ranked[0].paper_id == "a"
    assert ranked[0].score > ranked[1].score


def test_dedup_links_preprint_to_published_doi():
    preprint = PaperMetadata(
        paper_id="doi:10.1101/preprint",
        title="A foundation model for molecular design",
        doi="10.1101/preprint",
        published_doi="10.1000/published",
        is_preprint=True,
        peer_reviewed=False,
        review_status="preprint",
        source_hits=["biorxiv"],
    )
    published = PaperMetadata(
        paper_id="doi:10.1000/published",
        title="Published version with a changed title",
        doi="10.1000/published",
        source_hits=["pubmed"],
    )
    result = deduplicate([preprint, published])
    assert len(result) == 1
    assert set(result[0].source_hits) == {"biorxiv", "pubmed"}
    assert result[0].is_preprint is False
    assert result[0].peer_reviewed is True
    assert result[0].review_status == "published"


def test_preprint_penalty_is_relaxed_for_latest_intent():
    def papers():
        return [
            PaperMetadata(
                paper_id="published", title="Protein generation model",
                year=2026, source_hits=["openalex"]
            ),
            PaperMetadata(
                paper_id="preprint", title="Protein generation model",
                year=2026, source_hits=["biorxiv"], is_preprint=True,
                peer_reviewed=False, review_status="preprint"
            ),
        ]

    normal = {p.paper_id: p.score for p in rank_papers("protein model", papers())}
    latest = {
        p.paper_id: p.score
        for p in rank_papers("latest protein model", papers())
    }
    assert normal["published"] > normal["preprint"]
    assert (
        latest["preprint"] - latest["published"]
        > normal["preprint"] - normal["published"]
    )


def test_similar_titles_with_conflicting_dois_are_not_merged():
    papers = [
        PaperMetadata(
            paper_id="a",
            title="KRAS G12C resistance mechanisms in lung cancer",
            doi="10.1000/article-a",
            year=2024,
            authors=["Jane Doe"],
            source_hits=["openalex"],
        ),
        PaperMetadata(
            paper_id="b",
            title="KRAS G12C resistance mechanism in lung cancer",
            doi="10.1000/article-b",
            year=2024,
            authors=["Jane Doe"],
            source_hits=["europepmc"],
        ),
    ]
    assert len(deduplicate(papers)) == 2


def test_title_only_merge_requires_author_evidence():
    papers = [
        PaperMetadata(
            paper_id="a", title="A common review title", year=2024,
            source_hits=["openalex"]
        ),
        PaperMetadata(
            paper_id="b", title="A common review title", year=2024,
            source_hits=["semantic_scholar"]
        ),
    ]
    assert len(deduplicate(papers)) == 2


def test_conflicting_pmcids_are_not_merged_even_with_same_doi():
    papers = [
        PaperMetadata(
            paper_id="a", title="Paper", doi="10.1000/same", pmcid="PMC111",
            source_hits=["openalex"]
        ),
        PaperMetadata(
            paper_id="b", title="Paper", doi="10.1000/same", pmcid="PMC222",
            source_hits=["europepmc"]
        ),
    ]
    assert len(deduplicate(papers)) == 2


def test_merge_tracks_field_provenance_and_prefers_biomedical_metadata():
    papers = [
        PaperMetadata(
            paper_id="a",
            title="Paper",
            doi="10.1000/same",
            year=2023,
            journal="Repository metadata",
            source_hits=["openalex"],
            field_sources={
                "title": ["openalex"], "doi": ["openalex"],
                "year": ["openalex"], "journal": ["openalex"],
            },
            metadata_conflicts=[],
        ),
        PaperMetadata(
            paper_id="b",
            title="Paper",
            doi="10.1000/same",
            year=2024,
            journal="Biomedical Journal",
            pmcid="PMC123",
            source_hits=["europepmc"],
            field_sources={
                "title": ["europepmc"], "doi": ["europepmc"],
                "year": ["europepmc"], "journal": ["europepmc"],
                "pmcid": ["europepmc"],
            },
            metadata_conflicts=[],
        ),
    ]
    result = deduplicate(papers)[0]
    assert result.year == 2024
    assert result.journal == "Biomedical Journal"
    assert result.pmcid == "PMC123"
    assert result.field_sources["doi"] == ["openalex", "europepmc"]
    assert result.field_sources["pmcid"] == ["europepmc"]
    assert any(conflict["field"] == "year" for conflict in result.metadata_conflicts)
