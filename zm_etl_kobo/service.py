from api_etl.services import ConnectorETLService

from zm_etl_kobo.adapter import KoboAdapter
from zm_etl_kobo.source import KoboSource


class KoboETLService(ConnectorETLService):
    """KoboToolbox -> openIMIS individuals/households (new field registrations)."""
    source_cls = KoboSource
    adapter_cls = KoboAdapter
