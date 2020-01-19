from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pygments.formatter import Formatter
from pygments.styles.monokai import MonokaiStyle

from .config import Config
from .gif_encoder import GIFEncoder
from .opencv_encoder import OpenCVEncoder
from .text_format import irepr
from .text_painter import TextPainter
from .webp_encoder import WebPEncoder

WATERMARK = "Generated by vardbg"


def parse_hex_color(string):
    r = int(string[0:2], 16)
    g = int(string[2:4], 16)
    b = int(string[4:6], 16)

    return r, g, b, 255


def load_style(style):
    styles = {}
    formatter = Formatter(style=style)

    for token, params in formatter.style:
        color = parse_hex_color(params["color"]) if params["color"] else None
        # Italic and underline styles aren't supported, so just use bold for them
        bold = params["bold"] or params["italic"] or params["underline"]

        # Save style
        styles[token] = {"color": color, "bold": bold}

    return styles


class FrameRenderer:
    RED = 0
    GREEN = 1
    BLUE = 2

    def __init__(self, path, config_path):
        # Config
        self.cfg = Config(config_path)
        # Video encoder
        ext = Path(path).suffix.lower()[1:]
        if ext == "mp4":
            self.encoder = OpenCVEncoder(path, "mp4v", self.cfg.fps, self.cfg.w, self.cfg.h)
        elif ext == "gif":
            self.encoder = GIFEncoder(path, self.cfg.fps)
        elif ext == "webp":
            self.encoder = WebPEncoder(path, self.cfg.fps)
        else:
            raise ValueError(f"Unrecognized file extension '{ext}'")
        # Drawing context
        self.draw = None
        # Fonts
        self.body_font = ImageFont.truetype(*self.cfg.font_body)
        self.body_bold_font = ImageFont.truetype(*self.cfg.font_body_bold)
        self.caption_font = ImageFont.truetype(*self.cfg.font_caption)
        self.head_font = ImageFont.truetype(*self.cfg.font_heading)
        self.intro_font = ImageFont.truetype(*self.cfg.font_intro)

        # Sizes and positions to be calculated later
        self.sizes_populated = False
        # Code body size
        self.line_height = None
        self.body_cols = None
        self._body_rows = None
        self.body_rows = None
        # Output body start position
        self.out_x = None
        self.out_y = None
        # Output body size
        self.out_cols = None
        self.out_rows = None
        # Variable body start positions
        self.vars_x = None
        self.vars_y = None
        self.ovars_x = None
        self.ovars_y = None
        # Variable body size
        self.vars_cols = None
        self.vars_rows = None
        self.ovars_cols = None
        self.ovars_rows = None

        # Per-frame positions
        self.last_var_x = None
        self.last_var_y = None
        self.ref_var_x = None
        self.ref_var_y = None

        # Current video frame (image)
        self.frame = None

        # Write intro (if necessary)
        if self.cfg.intro_text and self.cfg.intro_time:
            self.write_intro()

    def calc_sizes(self):
        # Calculate text sizes
        w, h = self.draw.textsize("A", font=self.body_font)
        hw, hh = self.draw.textsize("A", font=self.head_font)
        _, mh = self.draw.textsize("`^Ag", font=self.body_font)
        _, ch = self.draw.textsize("1p", font=self.caption_font)

        # Code body size
        self.line_height = mh * self.cfg.line_height
        self.body_cols = round((self.cfg.var_x - self.cfg.sect_padding * 2) / w)
        self._body_rows = (self.cfg.out_y - self.cfg.sect_padding * 2 - ch) / self.line_height
        self.body_rows = int(self._body_rows)

        # Output body start position
        self.out_x = self.cfg.sect_padding
        self.out_y = self.cfg.out_y + self.cfg.head_padding * 2 + hh

        # Output body size
        self.out_cols = self.body_cols
        self.out_rows = round((self.cfg.h - self.out_y) / self.line_height)

        # Variable body start positions
        # Top-left X and Y for last variable section
        self.vars_x = self.cfg.var_x + self.cfg.sect_padding
        self.vars_y = self.cfg.head_padding * 2 + hh

        # Columns and rows for last variable section
        self.vars_cols = (self.cfg.w - self.cfg.var_x - self.cfg.sect_padding * 2) // w
        self.vars_rows = int((self.cfg.ovar_y - self.cfg.head_padding * 2 - hh) / self.line_height)

        # Top-left X and Y for other variables section
        self.ovars_x = self.vars_x
        self.ovars_y = self.cfg.ovar_y + self.vars_y

        # Columns and rows for other variables section
        self.ovars_cols = self.vars_cols
        ovars_h = self.cfg.h - self.cfg.ovar_y
        self.ovars_rows = int((ovars_h - self.cfg.sect_padding * 2) / self.line_height)

    def get_color(self, col):
        if col == self.RED:
            return self.cfg.red
        elif col == self.GREEN:
            return self.cfg.green
        else:
            return self.cfg.blue

    def draw_text_center(self, x, y, text, font, color):
        w, h = self.draw.textsize(text, font=font)
        self.draw.text((x - w / 2, y - h / 2), text, font=font, fill=color)

    def new_frame(self):
        # Create image
        self.frame = Image.new("RGB", (self.cfg.w, self.cfg.h), self.cfg.bg)
        # Create drawing context
        self.draw = ImageDraw.Draw(self.frame)

    def start_frame(self):
        self.new_frame()

        # Draw output section
        # Horizontal divider at 4/5 height
        self.draw.line(((0, self.cfg.out_y), (self.cfg.var_x, self.cfg.out_y)), fill=self.cfg.fg_body, width=1)
        # Label horizontally centered and padded
        out_center_x = self.cfg.var_x / 2
        out_y = self.cfg.out_y + self.cfg.head_padding
        self.draw_text_center(
            out_center_x, out_y, "Output", self.head_font, self.cfg.fg_heading,
        )

        # Draw variable section
        # Vertical divider at 2/3 width
        self.draw.line(((self.cfg.var_x, 0), (self.cfg.var_x, self.cfg.h)), fill=self.cfg.fg_body, width=1)
        # Label horizontally centered in the variable section and vertically padded
        var_center_x = self.cfg.var_x + ((self.cfg.w - self.cfg.var_x) / 2)
        self.draw_text_center(var_center_x, self.cfg.head_padding, "Last Variable", self.head_font, self.cfg.fg_heading)

        # Draw other variables section
        # Horizontal divider at 1/3 height
        self.draw.line(
            ((self.cfg.var_x, self.cfg.ovar_y), (self.cfg.w, self.cfg.ovar_y)), fill=self.cfg.fg_body, width=1
        )
        # Label similar to the first, but in the others section instead
        ovar_label_y = self.cfg.ovar_y + self.cfg.head_padding
        self.draw_text_center(var_center_x, ovar_label_y, "Other Variables", self.head_font, self.cfg.fg_heading)

        if not self.sizes_populated:
            self.calc_sizes()
            self.sizes_populated = True

    def finish_frame(self, var_state):
        # Bail out if there's no frame to finish
        if self.frame is None:
            return

        # Draw variable state (if available)
        if var_state is not None:
            self.draw_variables(var_state)

        if self.cfg.watermark:
            self.draw_watermark()

        self.encoder.write(self.frame)

    def write_intro(self):
        frames = round(self.cfg.intro_time * self.cfg.fps)
        for _ in range(frames):
            self.new_frame()
            x = self.cfg.w / 2
            y = self.cfg.h / 2
            self.draw_text_center(x, y, self.cfg.intro_text, self.intro_font, self.cfg.fg_heading)
            self.finish_frame(None)

    def draw_code(self, lines, cur_line):
        cur_idx = cur_line - 1

        # Construct list of (line, highlighted) tuples
        hlines = [(line, i == cur_idx) for i, line in enumerate(lines)]

        # Calculate start and end display indexes with an equivalent number of lines on both sides for context
        ctx_side_lines = (self._body_rows - 1) / 2
        start_idx = round(cur_idx - ctx_side_lines)
        end_idx = round(cur_idx + ctx_side_lines)
        # Accommodate for situations where not enough lines are available at the beginning
        if start_idx < 0:
            start_extra = abs(start_idx)
            end_idx += start_extra
            start_idx = 0
        # Slice selected section
        display_lines = hlines[start_idx:end_idx]

        # Construct painter
        x_start = self.cfg.sect_padding
        y_start = self.cfg.sect_padding + self.line_height
        x_end = self.cfg.var_x - self.cfg.sect_padding
        painter = TextPainter(self, x_start, y_start, self.body_cols, self.body_rows, x_end=x_end, show_truncate=False)

        # Render processed lines
        styles = load_style(MonokaiStyle)
        for i, (line, highlighted) in enumerate(display_lines):
            bg_color = self.cfg.highlight if highlighted else None

            for token, text in line:
                painter.write(text, bg_color=bg_color, **styles[token])

    def draw_output(self, lines):
        lines = lines[-self.out_rows :]
        painter = TextPainter(self, self.out_x, self.out_y, self.out_cols, self.out_rows)
        painter.write("\n".join(lines))

    def draw_exec(self, nr_times, cur, avg, total):
        plural = "" if nr_times == 1 else "s"
        text = f"Line executed {nr_times} time{plural} — current time elapsed: {cur}, average: {avg}, total: {total}"

        _, h = self.draw.textsize(text, font=self.caption_font)
        x = self.cfg.sect_padding
        y = self.cfg.out_y - self.cfg.sect_padding - h
        self.draw.text((x, y), text, font=self.caption_font)

    def draw_last_var(self, state):
        painter = TextPainter(self, self.vars_x, self.vars_y, self.vars_cols, self.vars_rows)

        # Draw variable name
        painter.write(state.name + " ")
        # Draw action with color
        self.last_var_x, self.last_var_y = painter.write(state.action + " ", bold=True, color=state.color)
        painter.new_line()

        # Draw remaining text
        painter.write(state.text)

    def draw_other_vars(self, state):
        painter = TextPainter(self, self.ovars_x, self.ovars_y, self.ovars_cols, self.ovars_rows)

        # Draw text
        for idx, (var, values) in enumerate(state.other_history):
            if values.ignored:
                continue

            if idx > 0:
                painter.write("\n\n")

            painter.write(var.name + ":")

            for v_idx, value in enumerate(values):  # sourcery off
                painter.write("\n    \u2022 ")

                # Reference highlighting for latest value and matching variables only
                if var.name == state.ref and v_idx == len(values) - 1:
                    v_pos = irepr(painter, value.value, state.value, bold=True, color=state.color, return_pos="H")
                    self.ref_var_x, self.ref_var_y = v_pos
                else:
                    irepr(painter, value.value)

    def draw_var_ref(self, state):
        # Calculate X position to route the line on
        # It should be as short as possible while not obscuring any variables or exceeding the scene width
        right_line_x = min(
            max(self.last_var_x, self.ref_var_x) + self.cfg.sect_padding, self.cfg.w - self.cfg.sect_padding / 2
        )

        sw, sh = self.draw.textsize(" ", font=self.body_font)

        # Draw the polyline
        self.draw.line(
            (
                (self.last_var_x, self.last_var_y),
                (right_line_x, self.last_var_y),
                (right_line_x, self.ref_var_y - sh),
                (self.ref_var_x, self.ref_var_y - sh),
                (self.ref_var_x, self.ref_var_y),
            ),
            fill=state.color,
            width=2,
        )

    def draw_variables(self, state):
        self.draw_other_vars(state)
        self.draw_last_var(state)

        if state.ref is not None:
            self.draw_var_ref(state)

    def draw_watermark(self):
        # Get target bottom-right position
        x = self.cfg.w - self.cfg.sect_padding
        y = self.cfg.h - self.cfg.sect_padding

        # Subtract text size to position it properly
        w, h = self.draw.textsize(WATERMARK, font=self.caption_font)
        x -= w
        y -= h

        # Draw text
        self.draw.text((x, y), WATERMARK, fill=self.cfg.fg_watermark, font=self.caption_font)

    def close(self, var_state):
        # Finish final frame
        self.finish_frame(var_state)
        # Close encoder
        self.encoder.stop()
