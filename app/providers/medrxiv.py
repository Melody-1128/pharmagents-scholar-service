from app.providers.biorxiv import RxivProvider


class MedRxivProvider(RxivProvider):
    name = "medrxiv"
    server = "medrxiv"
