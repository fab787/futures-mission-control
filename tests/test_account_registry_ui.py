from mission_control.account_registry_ui import build_dashboard


def test_registry_workspace_is_injected_into_existing_dashboard() -> None:
    source = '<nav><button data-tab="risk">Risk &amp; Approvals</button></nav><section class="section" id="integrations"></section></body>'
    result = build_dashboard(source)

    assert '<button data-tab="accounts">Accounts</button>' in result
    assert '<section class="section" id="accounts">' in result
    assert 'id="registry-create-form"' in result
    assert 'fetch("/api/accounts"' in result
    assert 'fetch("/api/risk/profiles"' in result
    assert 'data-tab="risk"' in result


def test_registry_workspace_injection_is_idempotent() -> None:
    source = '<nav><button data-tab="risk">Risk</button></nav><section class="section" id="integrations"></section></body>'
    once = build_dashboard(source)
    twice = build_dashboard(once)

    assert twice.count('data-tab="accounts"') == 1
    assert twice.count('id="accounts"') == 1
    assert twice.count('id="registry-create-form"') == 1
