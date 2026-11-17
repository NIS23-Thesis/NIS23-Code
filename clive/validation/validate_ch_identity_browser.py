"""Compatibility entrypoint: validates the new independent per-CH Model Learning UI.

The older ranked comparison and paginator were intentionally replaced with
one CH at a time and independent master/per-panel selectors.
"""
from validate_learning_panel_filters_browser import check

if __name__ == '__main__':
    for width, height in ((1440,900), (1280,800), (768,900), (390,844)):
        check(width, height)
