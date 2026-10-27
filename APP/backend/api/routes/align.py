# -*- coding: utf-8 -*-
"""
Alignment route composition.
Keep this module as the stable import entrypoint for app registration.
"""

from flask_restx import Namespace

from .align_models import build_align_export_response_model, build_align_models
from .align_resources import register_align_export_routes, register_align_routes


def register_align_namespace(api) -> None:
    ns = Namespace("align", description="Sequence alignment operations")
    models = build_align_models(ns)
    register_align_routes(ns, models)

    export_ns = Namespace("align/export", description="Export template CSV")
    export_res = build_align_export_response_model(export_ns)
    register_align_export_routes(export_ns, export_res)

    api.add_namespace(ns)
    api.add_namespace(export_ns)
