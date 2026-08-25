import json

from api_etl.sources import BaseHttpSource


class KoboSource(BaseHttpSource):
    """Pulls submissions from the KoboToolbox v2 API.

    Differs from an offset-paginated source in three ways, each of which bites if you
    get it wrong:

    1. Paging is by following the absolute `next` URL, which already carries its own
       `start`/`limit`/`query`. Re-appending params to it corrupts the cursor, so the
       core's `iter_next_url_pages` sends params on the first request only - while still
       re-sending auth headers on every request.
    2. Incremental filtering uses a Mongo-style `query` parameter on `_submission_time`,
       not a plain query-string date.
    3. `sort` ascending is REQUIRED. Without it a failure part way through pagination
       leaves a watermark that has skipped records, because the high-water mark would
       have been taken from an arbitrary page.
    """

    def pull(self):
        yield from self.iter_next_url_pages(first_params=self._first_params())

    def _first_params(self):
        cursor_field = self.cfg.incremental.cursor_field or "_submission_time"
        params = {
            "format": "json",
            "limit": self.cfg.source.batch_size,
            "sort": json.dumps({cursor_field: 1}),
        }
        if self.cfg.incremental.enabled:
            watermark = self.effective_watermark()
            if watermark:
                params["query"] = json.dumps({cursor_field: {"$gte": watermark}})
        return params
