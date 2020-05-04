"""
Terminology
original_view: the view in the regular editor, without it's own window
markdown_view: the markdown view, in the special window
preview_view: the preview view, in the special window
original_window: the regular window
preview_window: the window with the markdown file and the preview
"""


import os.path
import time
from functools import partial

import mdpopups
import sublime
import sublime_plugin
from mdpopups import frontmatter

from .ImageParser import imageparser

MD_VIEW_INFO = "md_view_info"
SETTING_DELAY_BETWEEN_UPDATES = "delay_between_updates"
PREVIEW_VIEWS = dict()

resources = {}

MD_FM = {
    "allow_code_wrap": False,
    "markdown_extensions": [
        "markdown.extensions.admonition",
        "markdown.extensions.attr_list",
        "markdown.extensions.def_list",
        "markdown.extensions.nl2br",
        {"markdown.extensions.smarty": {"smart_quotes": False}},
        "pymdownx.extrarawhtml",
        "pymdownx.keys",
        {"pymdownx.escapeall": {"hardbreak": True, "nbsp": True}},
        {"pymdownx.smartsymbols": {"ordinal_numbers": False}},
        "pymdownx.striphtml",
        "pymdownx.b64",
    ],
}


def plugin_loaded():
    global DELAY, SETTINGS
    resources["base64_404_image"] = parse_image_resource(
        get_resource("404.base64"))
    resources["base64_loading_image"] = parse_image_resource(
        get_resource("loading.base64")
    )
    resources["base64_invalid_image"] = parse_image_resource(
        get_resource("invalid.base64")
    )
    SETTINGS = get_settings()
    DELAY = SETTINGS.get(SETTING_DELAY_BETWEEN_UPDATES, 100)
    SETTINGS.add_on_change("key_changes", update_delay)


def plugin_unloaded():
    global SETTINGS
    SETTINGS.clear_on_change("key_changes")


def update_delay():
    try:
        global DELAY
        DELAY = int(SETTINGS.get(SETTING_DELAY_BETWEEN_UPDATES))
    except Exception as ex:
        print(ex)


class MdlpInsertCommand(sublime_plugin.TextCommand):
    def run(self, edit, point, content):
        self.view.insert(edit, point, content)


class MdlpEraseCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        region = sublime.Region(0, self.view.size())
        self.view.erase(edit, region)


class MarkdownLivePreviewBaseCommand:
    last_update = float()

    # & Only update if the time between updates
    # - is greater than our delay.
    def _skip_update(self):
        if time.time() - self.last_update > DELAY / 1000:
            self.last_update = time.time()
            return False
        return True

    def _preview_from_markdown(self, view):
        # Desc: Check if View is in Previews
        if view.id() not in PREVIEW_VIEWS.keys():
            return None

        if view.buffer_id() == 0:
            return None

        for enum_view in sublime.active_window().sheets():
            if enum_view.id() == PREVIEW_VIEWS[view.id()]:
                return enum_view

        return None

    def generate_content(self, view):
        total_region = sublime.Region(0, view.size())
        fm, content = frontmatter.get_frontmatter(view.substr(total_region))
        MD_FM.update(fm)
        content = "{}\n\n{}".format(
            mdpopups.format_frontmatter(
                MD_FM), self.render_checkboxes(content),
        )
        html_content = mdpopups.md2html(view, content).replace("<br>", "<br/>")

        file_name = view.file_name()
        basepath = os.path.dirname(file_name) if file_name else None
        html_content = imageparser(
            html_content, basepath, partial(
                self._update_preview, view), resources,
        )
        return html_content

    # & Unicode Checkboxes
    # - Provide unicode characters to replace checkboxes
    def render_checkboxes(self, content: str):
        if SETTINGS.get("render_checkboxes", False):
            font_size = SETTINGS.get("render_checkboxes_font_size", 16)
            return content.replace(
                "- [ ]",
                "<big style='font-size: {}px;'>&nbsp;&#9744;</big>".format(font_size)
                ).replace(
                    "- [x]",
                    "<big style='font-size: {}px;'>&nbsp;&#9745;</big>".format(font_size)
                )
        return content


class OpenMarkdownPreviewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global preview_view, PREVIEW_VIEWS
        """ Description: If the file is saved exists on disk, we close it,
        - and reopen it in a new window. Otherwise, we copy the content,
        - erase it all (to close the file without a dialog)
        - and re-insert it into a new view into a new window """

        original_view = self.view
        original_window_id = original_view.window().id()
        file_name = original_view.file_name()

        syntax_file = original_view.settings().get("syntax")

        if file_name:
            original_view.close()
        else:
            # the file isn't saved, we need to restore the content manually
            total_region = sublime.Region(0, original_view.size())
            content = original_view.substr(total_region)
            original_view.erase(edit, total_region)
            original_view.close()
            # FIXME: save the document to a temporary file, so that if we crash,
            # - the user doesn't lose what he wrote

        sublime.run_command("new_window")
        preview_window = sublime.active_window()

        preview_window.run_command(
            "set_layout",
            {
                "cols": [0.0, 0.5, 1.0],
                "rows": [0.0, 1.0],
                "cells": [[0, 0, 1, 1], [1, 0, 2, 1]],
            },
        )

        preview_window.focus_group(1)
        preview_view = mdpopups.new_html_sheet(
            window=preview_window,
            name="Preview",
            contents="")

        preview_window.focus_group(0)
        if file_name:
            markdown_view = preview_window.open_file(file_name)
        else:
            markdown_view = preview_window.new_file()
            markdown_view.run_command(
                "mdlp_insert", {"point": 0, "content": content})
            markdown_view.set_scratch(True)

        markdown_view.set_syntax_file(syntax_file)
        markdown_view.settings().set(
            MD_VIEW_INFO, {"original_window_id": original_window_id, },
        )
        PREVIEW_VIEWS[markdown_view.id()] = preview_view.id()

    def is_enabled(self):
        as_markdown = SETTINGS.get("syntax", ["Markdown"])
        return any(
            syntax in self.view.settings().get("syntax") for syntax in as_markdown
        )


class MarkdownLivePreviewListener(
    MarkdownLivePreviewBaseCommand, sublime_plugin.EventListener
):

    # FIXME: maybe we shouldn't restore the file in the original window...
    def on_pre_close(self, view):
        """
        Close the view in the preview window,
        and store information for the on_close
        listener (see doc there)
        """
        if not view.settings().get(MD_VIEW_INFO):
            return

        self.markdown_view = view
        self.preview_window = view.window()
        self.file_name = view.file_name()

        if self.file_name is None:
            total_region = sublime.Region(0, view.size())
            self.content = view.substr(total_region)
            view.run_command("mdlp_erase")
        else:
            self.content = None

    def on_load_async(self, view):
        infos = view.settings().get(MD_VIEW_INFO)
        if not infos:
            return
        self._update_preview(view)

    def on_close(self, view):
        """
        Use the information saved to restore the view
        as an original_view
        """
        infos = view.settings().get(MD_VIEW_INFO)
        if not infos:
            return

        assert (
            view.id() == self.markdown_view.id()
        ), "pre_close view.id() != close view.id()"
        self.preview_window.run_command("close_window")

        # find the window with the right id
        original_window = next(
            window
            for window in sublime.windows()
            if window.id() == infos["original_window_id"]
        )
        if self.file_name:
            original_window.open_file(self.file_name)
        else:
            assert view.is_scratch(), (
                "markdown view of an unsaved file should " "be a scratch"
            )
            # note here that this is called original_view, because it's what
            # semantically makes sense, but this original_view.id() will be
            # different than the one that we closed first to reopen in
            # the preview window shouldn't cause any
            # trouble though
            original_view = original_window.new_file()
            original_view.run_command(
                "mdlp_insert", {"point": 0, "content": self.content}
            )

            original_view.set_syntax_file(view.settings().get("syntax"))
        global PREVIEW_VIEWS
        if view.id() in PREVIEW_VIEWS.keys():
            del PREVIEW_VIEWS[view.id()]

    # here, views are NOT treated independently, which is theoretically wrong
    # but in practice, you can only edit one markdown file at a time,
    # so it doesn't really matter.
    # @min_time_between_call(.5)
    def on_modified_async(self, view):
        if not view.settings().get(MD_VIEW_INFO):
            return

        # we schedule an update, which won't run if an
        sublime.set_timeout(partial(self._update_preview, view), DELAY)

    def _update_preview(self, view):
        # if the buffer id is 0, that means that the view has been
        # closed This check is needed since a this function is used as a
        # callback for when images are loaded from the internet (ie. it could
        # finish loading *after* the user closes the view)
        if self._skip_update():
            return

        preview_view = self._preview_from_markdown(view)
        if preview_view is None:
            return

        mdpopups.update_html_sheet(
           sheet=preview_view,
           contents=self.generate_content(view),
           md=False)


def get_settings():
    return sublime.load_settings("MarkdownLivePreview.sublime-settings")


def get_resource(resource):
    path = "Packages/MarkdownLivePreview/resources/" + resource
    abs_path = os.path.join(sublime.packages_path(), "..", path)
    if os.path.isfile(abs_path):
        with open(abs_path, "r") as fp:
            return fp.read()
    return sublime.load_resource(path)


def parse_image_resource(text):
    base64_image = text.splitlines()
    return base64_image
