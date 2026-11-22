"""The free loop builder: one H.264 pane per basket member, cropped around the product."""

from pathlib import Path

from scripts.build_bundle_loops import compose_command, source_for

from app.modules.creative.application.campaigns import CampaignMedia


def test_a_category_without_footage_uses_its_approved_photo(tmp_path) -> None:
    from scripts.build_bundle_loops import ASSETS

    media = {
        "category:ice_cream": CampaignMedia(
            photo_url="/campaign-assets/photos/cl-helado-playa.webp"
        )
    }
    assert source_for("savory", "ice_cream", media) == ASSETS / "photos/cl-helado-playa.webp"


def test_photo_loops_have_motion_and_no_logos_or_text() -> None:
    cmd = compose_command("ffmpeg", [(Path("cl-nectar.webp"), "#ffffff")], Path("out.mp4"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "zoompan" in graph and "sin(" in graph
    assert "-loop" in cmd and "drawtext" not in graph


def test_a_bundle_is_one_graded_pane_per_member_and_plays_in_chromium() -> None:
    cmd = compose_command(
        "ffmpeg",
        [(Path("coffee.mp4"), "#f5bb84"), (Path("energy.mp4"), "#b9adff")],
        Path("out.mp4"),
    )
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "hstack=inputs=2" in graph
    assert "color=c=0xb9adff:s=640x720" in " ".join(cmd)  # Rayo's own grade
    assert "iw*0.8-640/2" in graph and "iw*0.78-640/2" in graph  # framed on the product
    assert "drawtext" not in graph  # no text, logos or prices inside video (ADR 0003)
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and "+faststart" in cmd
    assert cmd[-1] == "out.mp4"


def test_a_solo_loop_is_a_single_graded_pane() -> None:
    cmd = compose_command("ffmpeg", [(Path("beer.mp4"), "#f6d15a")], Path("o.mp4"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "hstack" not in graph and "s=1280x720" in " ".join(cmd)
