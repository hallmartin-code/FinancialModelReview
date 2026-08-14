"""Extraction layer: source files -> a structured payload, via Claude.

This is the only layer that makes network calls. It reads; it never judges. Every
value it returns carries the page, slide, or cell it came from, and anything the
source does not state comes back empty rather than invented.
"""
