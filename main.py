import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from typing import Any
import io
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPDF
import base64
import requests
import re
from typing import Tuple, Optional
from devtools import debug

channel_id_to_printer_settings_map = {
    "C0A9B0T1938": {
        "printer_id": 75094179,
        "label_width_mm": 100,
        "label_height_mm": 75,
    },
    "C0A9GFLL64B": {
        "printer_id": 75093701,
        "label_width_mm": 192,
        "label_height_mm": 102,
    },
    "C0AAWHQUKR6": {
        "printer_id": 72775345,
        "label_width_mm": 192,
        "label_height_mm": 102,
    },
}

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))


def wrap_text_to_width(text, font_name, font_size, max_width_pt):
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for w in words[1:]:
        candidate = current + " " + w
        if pdfmetrics.stringWidth(candidate, font_name, font_size) <= max_width_pt:
            current = candidate
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return lines


def layout_lines(text, font_name, font_size, max_text_width_pt):
    if "\n" in text:
        return text.splitlines() or [""]
    return wrap_text_to_width(text, font_name, font_size, max_text_width_pt)


def measure_text_box(lines, font_name, font_size, leading_pt, padding_pt):
    line_widths = [pdfmetrics.stringWidth(line, font_name, font_size) for line in lines]
    text_w = max(line_widths) if line_widths else 0
    text_h = len(lines) * leading_pt
    return text_w + 2 * padding_pt, text_h + 2 * padding_pt


def load_and_scale_svg(svg_path, target_w_pt=None, target_h_pt=None):
    """
    Returns (drawing, scaled_w, scaled_h). Placement should use scaled_w/h,
    not drawing.width/height, because svglib may not update them after scaling.
    """
    drawing = svg2rlg(svg_path)
    if drawing is None:
        raise ValueError(f"Could not load SVG: {svg_path}")

    orig_w = float(drawing.width or 0)
    orig_h = float(drawing.height or 0)

    # If SVG provides no size, we cannot scale reliably; draw at whatever it has.
    if orig_w <= 0 or orig_h <= 0:
        scaled_w = float(drawing.width or 0)
        scaled_h = float(drawing.height or 0)
        return drawing, scaled_w, scaled_h

    # Determine uniform scale
    if target_w_pt and target_h_pt:
        scale = min(target_w_pt / orig_w, target_h_pt / orig_h)
    elif target_w_pt:
        scale = target_w_pt / orig_w
    elif target_h_pt:
        scale = target_h_pt / orig_h
    else:
        scale = 1.0

    drawing.scale(scale, scale)

    scaled_w = orig_w * scale
    scaled_h = orig_h * scale

    # Optional but helpful: update width/height so other code (and some renderers) behave
    drawing.width = scaled_w
    drawing.height = scaled_h

    return drawing, scaled_w, scaled_h


def draw_svg_bottom_right(c, drawing, scaled_w, margin_pt, page_w):
    """
    Draw pre-scaled drawing at bottom-right with margin.
    Uses scaled_w for correct x placement.
    """
    x = page_w - margin_pt - scaled_w
    y = margin_pt
    renderPDF.draw(drawing, c, x, y)


def add_page_with_text(
    c, text, font_size, font_name, page_w, page_h, margin, padding, leading_factor
):
    # Safe area: reserve a bottom band for logo + margins + gap
    safe_left = margin
    safe_right = page_w - margin
    safe_bottom = margin
    safe_top = page_h

    safe_w = max(0, safe_right - safe_left)
    safe_h = max(0, safe_top - safe_bottom)

    max_box_w = safe_w
    max_box_h = safe_h

    # Fit textbox (shrink font if needed) within safe area
    current_size = float(font_size)
    while True:
        leading = current_size * leading_factor
        max_text_w = max_box_w - 2 * padding

        lines = layout_lines(text, font_name, current_size, max_text_w)
        box_w, box_h = measure_text_box(
            lines, font_name, current_size, leading, padding
        )

        if box_w <= max_box_w and box_h <= max_box_h:
            break

        current_size -= 0.5
        if current_size < 6:
            break

    leading = current_size * leading_factor
    max_text_w = max_box_w - 2 * padding
    lines = layout_lines(text, font_name, current_size, max_text_w)
    box_w, box_h = measure_text_box(lines, font_name, current_size, leading, padding)

    # Center box inside safe area
    box_x = safe_left + (safe_w - box_w) / 2.0
    box_y = safe_bottom + (safe_h - box_h) / 2.0

    c.setLineWidth(1)
    # c.rect(box_x, box_y, box_w, box_h, stroke=1, fill=0)

    c.setFont(font_name, current_size)
    text_x = box_x + padding
    text_top = box_y + box_h - padding
    text_y = text_top - current_size  # baseline approximation

    for line in lines:
        c.drawString(text_x, text_y, line)
        text_y -= leading


def create_pdf_with_safe_area_centered_textbox(
    text: str | list[str],
    page_w_mm=100,
    page_h_mm=75,
    margin_mm=4,
    font_name="Helvetica",
    font_size=200,
    leading_factor=1.2,
    padding_mm=2,
):
    page_w = page_w_mm * mm
    page_h = page_h_mm * mm
    margin = margin_mm * mm
    padding = padding_mm * mm

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_w, page_h))

    if isinstance(text, str):
        text = [text]

    for t in text:
        if t == "":
            continue
        add_page_with_text(
            c, t, font_size, font_name, page_w, page_h, margin, padding, leading_factor
        )
        c.showPage()

    c.save()

    return buf.getvalue()


def print_pdf_with_printnode(
    printer_id: int,
    pdf_bytes: bytes,
    *,
    title: str = "Print job",
    source: str = "python",
    qty: int = 1,
    options: dict | None = None,
    timeout_s: int = 30,
) -> int:
    """
    Sends a PDF to PrintNode using pdf_base64 and returns the created printJob id (integer).
    """

    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    payload = {
        "printerId": printer_id,
        "title": title,
        "source": source,
        "contentType": "pdf_base64",
        "content": pdf_b64,
        "qty": int(qty),
    }
    if options:
        payload["options"] = options

    r = requests.post(
        "https://api.printnode.com/printjobs",
        auth=(os.environ.get("PRINTNODE_API_KEY"), ""),
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout_s,
    )

    if r.status_code != 201:
        try:
            details = r.json()
        except Exception:
            details = r.text
        raise RuntimeError(f"PrintNode error {r.status_code}: {details}")

    return int(r.json())


def parse_qty(text: str) -> Tuple[Optional[int], str]:
    """
    Finds 'qty:NN' (NN 1..30), returns (qty_or_None, cleaned_text).
    Removes the first valid qty token and normalizes whitespace.
    """
    _QTY_RE = re.compile(r"(?i)(?:^|\s)qty:(\d{1,2})(?=\s|$)")
    m = _QTY_RE.search(text)
    if not m:
        return 1, text.strip()

    qty = int(m.group(1))
    if not (1 <= qty <= 30):
        return 1, text.strip()

    cleaned = (text[: m.start()] + text[m.end() :]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return qty, cleaned


@app.command("/l")
def hello_command(body, ack, respond, client, logger):
    qty, clean_text = parse_qty(body["text"])

    if printer_settings := channel_id_to_printer_settings_map.get(body["channel_id"]):
        pdf_bytes = create_pdf_with_safe_area_centered_textbox(
            clean_text,
            page_w_mm=printer_settings.get("label_width_mm"),
            page_h_mm=printer_settings.get("label_height_mm"),
        )

        # with open("test.pdf", "wb") as f:
        #     f.write(pdf_bytes)

        print_pdf_with_printnode(printer_settings.get("printer_id"), pdf_bytes, qty=qty)
        ack("Printing now")
    else:
        ack("No label printer configured for this channel")


def print_slack_pdf_file(client, printer_id, slack_file_private_download_url):
    resp = requests.get(
        slack_file_private_download_url,
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.content

    print_pdf_with_printnode(printer_id, data)


def print_slack_txt_file(client, printer_settings, slack_file_private_download_url):
    resp = requests.get(
        slack_file_private_download_url,
        headers={"Authorization": f"Bearer {client.token}"},
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.content
    lines = data.decode("utf-8", errors="replace").splitlines()
    lines = [s for s in lines if s.strip()]


    pdf_bytes = create_pdf_with_safe_area_centered_textbox(
        lines,
        page_w_mm=printer_settings.get("label_width_mm"),
        page_h_mm=printer_settings.get("label_height_mm"),
    )

    print_pdf_with_printnode(printer_settings.get("printer_id"), pdf_bytes, qty=1)

    return len(lines)


@app.event("file_shared")
def handle_file_shared_events(body, ack, respond, client, logger):
    file_info = client.files_info(file=body["event"]["file_id"])

    # debug(file_info["file"])

    if printer_settings := channel_id_to_printer_settings_map.get(
        body["event"]["channel_id"]
    ):
        if (
            file_info["file"]["filetype"] != "pdf"
            and file_info["file"]["filetype"] != "text"
        ):
            client.chat_postMessage(
                channel=body["event"]["channel_id"],
                text="Only PDF and plain text files are supported.",
            )
            return

        download_url = file_info["file"]["url_private_download"]

        if file_info["file"]["filetype"] == "pdf":
            print("Printing PDF posted to the Slack Channel")
            print_slack_pdf_file(
                client, printer_settings.get("printer_id"), download_url
            )
            client.chat_postMessage(
                channel=body["event"]["channel_id"],
                text=":printer: Printing the posted PDF file :point_up:",
            )

        if file_info["file"]["filetype"] == "text":
            print("Printing text file posted to the Slack Channel")
            label_qty = print_slack_txt_file(client, printer_settings, download_url)
            client.chat_postMessage(
                channel=body["event"]["channel_id"],
                text=f":printer: Printing {label_qty} labels based the posted text file :point_up:",
            )

    else:
        client.chat_postMessage(
            channel=body["event"]["channel_id"],
            text="No label printer is designated to handle this channel.",
        )


# Start your app
if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
