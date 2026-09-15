"""CI guard: every admin-chat v1 route remains behind bearer authentication."""

from fastapi.routing import APIRoute

from src.modules.admin_chat.router import router


def _calls(dependant):
    if dependant.call is not None:
        yield getattr(dependant.call, "__name__", "")
    for child in dependant.dependencies:
        yield from _calls(child)


def test_admin_chat_routes_keep_an_auth_dependency() -> None:
    """Adding an endpoint cannot accidentally make the private router public."""

    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert routes
    unprotected = [
        route.path
        for route in routes
        if "get_current_claims" not in set(_calls(route.dependant))
    ]
    assert not unprotected, f"Admin chat routes missing auth dependency: {unprotected}"


def test_campaign_import_routes_require_manager_not_only_a_valid_token() -> None:
    upload = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/workflows/{workflow_id}/imports")
    )
    status = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/imports/{import_id}")
    )
    for route in (upload, status):
        assert "_dep" in set(_calls(route.dependant))


def test_campaign_import_upload_has_a_workflow_revision_precondition() -> None:
    upload = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/workflows/{workflow_id}/imports")
    )

    assert "expected_revision" in {parameter.name for parameter in upload.dependant.body_params}


def test_legacy_outbound_batch_actions_also_use_the_trusted_client_ip_dependency() -> None:
    batch_action = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/batches/{batch_id}/actions")
    )

    assert "get_client_ip" in set(_calls(batch_action.dependant))
