from api_etl.adapters import BaseMappingAdapter


class KoboAdapter(BaseMappingAdapter):
    """Maps KoboToolbox submissions onto the individual import columns.

    Kobo flattens repeat groups into slash-separated keys (`household/hoh_first_name`),
    which `BaseMappingAdapter.resolve_path` already handles, so the mapping is pure
    configuration. This class exists as a stable type to register and as the place for
    Kobo-specific handling should a form ever need it.
    """
