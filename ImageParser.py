import base64
import concurrent.futures
import os.path
import re
import urllib.request
from functools import partial

import bs4

__all__ = ("imageparser",)


# FIXME: how do I choose how many workers I want? Does thread pool reuse threads or
#        does it stupidly throw them out? (we could implement something of our own)
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)


def imageparser(html, basepath, re_render, resources):
    soup = bs4.BeautifulSoup(html, "html.parser")
    for img_element in soup.find_all("img"):
        src = img_element["src"]

        # already in base64, or something of the like
        # FIXME: what other types are possible? Are they handled by ST? If not, could we
        #        convert it into base64? is it worth the effort?
        if src.startswith("data:image/"):
            continue
        if src.startswith("http://") or src.startswith("https://"):
            path = src
        elif src.startswith("file://"):
            path = src[len("file://") :]
        else:
            # expanduser: ~ -> /home/math2001
            # realpath: simplify that paths so that we don't have duplicated caches
            path = os.path.realpath(os.path.expanduser(os.path.join(basepath, src)))

        base64 = get_base64_image(path, re_render, resources)

        img_element["src"] = base64

    return re.sub(
        "(<!--.*?-->)",
        "",
        "{}".format(soup)
        .replace("<br/>", "<br />")
        .replace("</br>", "<br>")
        .replace("<hr/>", "<hr />"),
        flags=re.DOTALL,
    )


images_cache = {}
images_loading = []


def get_base64_image(path, re_render, resources):
    """ Gets the base64 for the image (local and remote images). re_render is a
    callback which is called when we finish loading an image from the internet
    to trigger an update of the preview (the image will then be loaded from the cache)
    return base64_data, (width, height)
    """

    def callback(path, resources, future):
        # altering images_cache is "safe" to do because callback is called in the same
        # thread as add_done_callback:
        # > Added callables are called in the order that they were added and are always
        # > called in a thread belonging to the process that added them
        # > --- Python docs
        try:
            images_cache[path] = future.result()
        except urllib.error.HTTPError as e:
            images_cache[path] = resources["base64_404_image"]
            print("Error loading {!r}: {!r}".format(path, e))

        images_loading.remove(path)

        # we render, which means this function will be called again, but this time, we
        # will read from the cache
        re_render()

    if path in images_cache:
        return images_cache[path]

    if path.startswith("http://") or path.startswith("https://"):
        # FIXME: submiting a load of loaders, we should only have one
        if path not in images_loading:
            executor.submit(load_image, path).add_done_callback(
                partial(callback, path, resources)
            )
            images_loading.append(path)
        return resources["base64_loading_image"]

    if not os.path.isfile(path):
        return resources["base64_invalid_image"]

    with open(path, "rb") as fhandle:
        image_content = fhandle.read()

        image = "data:image/png;base64," + base64.b64encode(image_content).decode(
            "utf-8"
        )
        images_cache[path] = image
        return images_cache[path]


def load_image(url):
    with urllib.request.urlopen(url, timeout=60) as conn:
        image_content = conn.read()

        content_type = conn.info().get_content_type()
        if "image" not in content_type:
            raise ValueError(
                "{!r} doesn't point to an image, but to a {!r}".format(
                    url, content_type
                )
            )
        return "data:image/png;base64," + base64.b64encode(image_content).decode(
            "utf-8"
        )
