# -*- coding: utf-8 -*-
"""
Region lookup namespace: expose APIs that map Sprinzl numbering to regions.
"""
from flask_restx import Namespace, Resource, fields
from flask import request
from backend.domain.alignment.number_to_region import number_to_region


def register_region_namespace(api):
    ns = Namespace("regions", description="Lookup tRNA regions by numbering")

    result_model = ns.model(
        "RegionLookupResult",
        {
            "input": fields.String(description="Original identifier", example="73"),
            "region": fields.String(description="Resolved region name", example="Aminoacyl arm 3' end"),
            "error": fields.String(description="Error message if lookup failed"),
        },
    )

    request_model = ns.model(
        "RegionLookupRequest",
        {
            "numbers": fields.List(
                fields.String,
                required=False,
                description="List of numbering strings (Sprinzl number or sequence index). Either `numbers` or `number` is required.",
                example=["73", "V1", "17a"],
                default=["73", "V1", "17a"],
            ),
            "number": fields.String(
                required=False,
                description="Single numbering string (use if you don't want an array). Either `number` or `numbers` is required.",
                example="72",
            ),
        },
    )

    response_model = ns.model(
        "RegionLookupResponse",
        {"results": fields.List(fields.Nested(result_model))},
    )

    @ns.route("/lookup", strict_slashes=False)
    class RegionLookup(Resource):
        @ns.expect(request_model, validate=True)
        @ns.response(200, "Success", response_model)
        @ns.response(400, "Bad Request")
        @ns.marshal_with(response_model)
        def post(self):
            """
            Convert Sprinzl numbers (and variants such as V1, 17a, etc.)
            into their corresponding tRNA regions. Supports single value
            via `number` or batch via `numbers`.
            """
            data = request.get_json(force=True) or {}
            numbers = data.get("numbers")
            if numbers is None:
                single = data.get("number")
                if single is None:
                    ns.abort(400, "Please provide `number` or `numbers`.")
                numbers = [single]
            if not isinstance(numbers, list):
                ns.abort(400, "`numbers` must be an array of strings.")

            results = []
            for item in numbers:
                identifier = str(item).strip()
                if not identifier:
                    results.append(
                        {"input": str(item), "region": None, "error": "Empty identifier."}
                    )
                    continue
                try:
                    region = number_to_region(identifier)
                    results.append({"input": identifier, "region": region, "error": None})
                except ValueError as exc:
                    results.append({"input": identifier, "region": None, "error": str(exc)})

            return {"results": results}

    api.add_namespace(ns)
