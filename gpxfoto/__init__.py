"""Geotagowanie zdjęć na podstawie trasy GPX.

Zapisuje wyłącznie metadane GPS (przez exiftool). Dane obrazu nie są
ponownie kompresowane, a program po każdym zapisie sprawdza sumą
kontrolną, że pozostały identyczne co do bajta.
"""
