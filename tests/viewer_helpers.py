"""Interact with the explorer and the standalone SDK example pages."""

import json


def select_layer(page, value=None, *, label=None, index=None):
    if not page.locator("#layer-search").count():
        return page.get_by_label("Dataset", exact=True).select_option(
            **(
                {"label": label}
                if label is not None
                else {"index": index}
                if index is not None
                else {"value": value}
            )
        )
    page.get_by_role("tab", name="Layers", exact=False).click()
    if index is not None:
        page.get_by_label("Search layers").fill("")
        page.locator(".layer-row").nth(index).click()
    else:
        if label is None:
            try:
                label = " / ".join(json.loads(value))
            except (ValueError, TypeError):
                label = value
        page.get_by_label("Search layers").fill(label)
        page.get_by_role("button", name=label, exact=True).click()


def style_tab(page):
    if page.get_by_role("tab", name="Style", exact=True).count():
        page.get_by_role("tab", name="Style", exact=True).click()


def share_view(page):
    from playwright.sync_api import expect

    page.get_by_role("button", name="Share view", exact=True).click()
    expect(page.locator("#share-link")).not_to_have_value("")
    page.get_by_role("button", name="Close share dialog").click()


def view_link(page, render, *, center, zoom):
    return page.evaluate(
        """async ({render, view}) => {
            const {Client, createState, encodeState} = await import(new URL('sdk/index.js', location.href));
            const baseUrl = new URL(document.body.dataset.service, location.href);
            const client = new Client(baseUrl);
            const identity = await client.connect();
            const state = await createState({baseUrl, profile: client.profile, render, view,
                sourceRevision: identity.source_revision || document.body.dataset.sourceRevision});
            const link = new URL(location.href);
            link.hash = encodeState(state);
            return link.href;
        }""",
        {"render": render, "view": {"center": center, "zoom": zoom}},
    )


def settled(page):
    page.wait_for_function(
        """() => {
        const tiles = [...document.querySelectorAll('.leaflet-tile')];
        return document.querySelector('#workspace')?.getAttribute('aria-busy') !== 'true'
            && tiles.length > 0 && tiles.every(t => t.naturalWidth > 0);
    }""",
        timeout=30000,
    )
