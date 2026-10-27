# -*- coding: utf-8 -*-
"""Read-only literature endpoints over the indexed PDF mirror and public indices."""
from __future__ import annotations

from flask import request
from flask_restx import Namespace, Resource

from backend.domain.literature import repository, search


def register_literature_namespace(api) -> None:
    ns = Namespace("literature", description="Reference lookup for reviewed tRNA mappings")

    @ns.route("/search", strict_slashes=False)
    class LiteratureSearch(Resource):
        @ns.doc(
            params={
                "q": "Free-text question in English.",
                "limit": "Maximum citations (1-20).",
                "categories": "Comma-separated categories to restrict the local mirror.",
                "public": "Set 0 to search only the locally mirrored PDFs.",
            }
        )
        def get(self):
            query = str(request.args.get("q") or "").strip()
            if not query:
                ns.abort(400, "Parameter `q` is required.")
            try:
                limit = max(1, min(int(request.args.get("limit") or 8), 20))
            except ValueError:
                ns.abort(400, "Parameter `limit` must be an integer.")
            categories = tuple(
                item.strip()
                for item in str(request.args.get("categories") or "").split(",")
                if item.strip()
            )
            include_public = str(request.args.get("public") or "1").strip() not in {"0", "false", "no"}
            try:
                citations = search.gather(
                    [query],
                    total_limit=limit,
                    per_query_limit=limit,
                    include_public=include_public,
                )
                return {
                    "query": query,
                    "categories": list(categories) or None,
                    "citations": citations,
                }, 200
            except Exception as exc:
                ns.abort(500, f"Literature search failed: {exc}")

    @ns.route("/stats", strict_slashes=False)
    class LiteratureStats(Resource):
        @ns.doc(description="Size and category mix of the locally mirrored corpus.")
        def get(self):
            try:
                return repository.corpus_stats(), 200
            except Exception as exc:
                ns.abort(500, f"Literature corpus stats failed: {exc}")

    api.add_namespace(ns)
