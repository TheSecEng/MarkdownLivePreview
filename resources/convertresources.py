""" A small script to convert the images into base64 data """

from base64 import b64encode


def make_cache(image_name):
    with open("{}.png".format(image_name), "rb") as png, open(
        "{}.base64".format(image_name), "wb"
    ) as base64:
        png.seek(0)
        base64.write(b"data:image/png;base64,")
        base64.write(b64encode(png.read()))


make_cache("404")
make_cache("loading")
make_cache("invalid_image")
