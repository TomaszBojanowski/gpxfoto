"""Geotagging photos from a GPX track.

Only GPS metadata is written (with exiftool). Image data is never
re-encoded, and after every write a checksum confirms that it stayed
identical byte for byte.
"""
