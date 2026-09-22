"""
============================================================================
STREAMLIT APP - GAD / BAFFLE PIPELINE
============================================================================
Upload the two source DXFs:

    <PREFIX>_GAD.DXF               - the full General Arrangement Drawing
    <PREFIX>_CUTTING MATERIAL.DXF  - the sheet-metal nesting/cutting layout

and this app runs the full gad_baffle_pipeline.py pipeline on them, then
shows:

  LEFT  - the capsule-vs-baffle overlay PNG (Step 7's output)
  RIGHT - the merged capsule/baffle match JSON (Step 8's output), rendered
          as one card per match: green if the matched hole's radius equals
          the capsule's own radius, red if it doesn't. Clicking a card's
          "Highlight" button redraws the overlay image with that capsule
          and its matched hole ringed in yellow, so it's easy to see where
          that particular match sits on the drawing.

Run with:  streamlit run app.py
============================================================================
"""

import json
import os
import shutil
import tempfile

import streamlit as st

import gad_baffle_pipeline as pipeline

st.set_page_config(page_title="GAD / Baffle Match Viewer", layout="wide")

RADIUS_MATCH_TOL = 0.05  # drawing units - within this, a hole/capsule radius counts as "the same"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def save_upload(uploaded_file, dest_dir, filename):
    """Write a Streamlit UploadedFile to `dest_dir/filename` and return the path."""
    dest_path = os.path.join(dest_dir, filename)
    with open(dest_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return dest_path


@st.cache_data(show_spinner=False)
def run_pipeline_cached(gad_bytes, cutting_bytes, gad_name, cutting_name):
    """
    Run the full pipeline in a fresh temp working dir and return everything
    the UI needs: the merged-match dict and the raw overlay PNG bytes.

    Cached on the *content* of the two uploaded files (gad_bytes/cutting_bytes)
    so re-running the app with the same two files doesn't re-run the pipeline.
    """
    work_dir = tempfile.mkdtemp(prefix="gad_pipeline_")
    gad_path = os.path.join(work_dir, gad_name)
    cutting_path = os.path.join(work_dir, cutting_name)
    with open(gad_path, "wb") as f:
        f.write(gad_bytes)
    with open(cutting_path, "wb") as f:
        f.write(cutting_bytes)

    paths = pipeline.build_paths(gad_path, cutting_path,
                                  output_dir=os.path.join(work_dir, "output"))
    pipeline.run_pipeline(paths)

    with open(paths["merged_match_json"]) as f:
        merged = json.load(f)
    with open(paths["trace_diff_png"], "rb") as f:
        overlay_png_bytes = f.read()

    return merged, overlay_png_bytes, paths


def render_highlighted_overlay(paths, capsule_index):
    """Re-run just Step 7 with a highlight on `capsule_index`, return PNG bytes."""
    highlight_dir = tempfile.mkdtemp(prefix="gad_highlight_")
    out_png = os.path.join(highlight_dir, "highlight_overlay.png")
    out_json = os.path.join(highlight_dir, "highlight_diff.json")
    pipeline.trace_and_diff_dxfs(
        paths["circle_extract_dxf"], paths["baffle_dxf"], out_png, out_json,
        highlight_capsule_index=capsule_index,
    )
    with open(out_png, "rb") as f:
        data = f.read()
    shutil.rmtree(highlight_dir, ignore_errors=True)
    return data


def radii_match(a, b, tol=RADIUS_MATCH_TOL):
    return a is not None and b is not None and abs(a - b) <= tol


# ----------------------------------------------------------------------------
# sidebar: uploads
# ----------------------------------------------------------------------------

st.sidebar.header("1. Upload drawings")
gad_file = st.sidebar.file_uploader("GAD drawing (e.g. 2193000764_GAD.DXF)", type=["dxf", "DXF"])
cutting_file = st.sidebar.file_uploader(
    "Cutting-material drawing (e.g. 2193000764_CUTTING MATERIAL.DXF)", type=["dxf", "DXF"]
)
run_clicked = st.sidebar.button("Run pipeline", type="primary", disabled=not (gad_file and cutting_file))

if run_clicked:
    st.session_state.pop("selected_capsule_index", None)
    with st.spinner("Running capsule/baffle pipeline..."):
        try:
            merged, overlay_png_bytes, paths = run_pipeline_cached(
                gad_file.getvalue(), cutting_file.getvalue(), gad_file.name, cutting_file.name,
            )
            st.session_state["merged"] = merged
            st.session_state["overlay_png_bytes"] = overlay_png_bytes
            st.session_state["paths"] = paths
        except Exception as e:
            st.session_state.pop("merged", None)
            st.sidebar.error(f"Pipeline failed: {e}")

# ----------------------------------------------------------------------------
# main view
# ----------------------------------------------------------------------------

st.title("GAD ↔ Baffle Capsule Match Viewer")

if "merged" not in st.session_state:
    st.info("Upload the GAD DXF and the CUTTING MATERIAL DXF in the sidebar, then click **Run pipeline**.")
    st.stop()

merged = st.session_state["merged"]
paths = st.session_state["paths"]
selected = st.session_state.get("selected_capsule_index")

left, right = st.columns([3, 2])

with left:
    st.subheader("Overlay" + (f" — highlighting capsule #{selected}" if selected is not None else ""))
    if selected is not None:
        with st.spinner("Re-rendering overlay with highlight..."):
            image_bytes = render_highlighted_overlay(paths, selected)
        if st.button("Clear highlight"):
            st.session_state.pop("selected_capsule_index", None)
            st.rerun()
    else:
        image_bytes = st.session_state["overlay_png_bytes"]
    st.image(image_bytes, use_container_width=True)

with right:
    st.subheader("Capsule ↔ hole matches")
    st.caption(f"outer_radius_gad = **{merged['outer_radius_gad']}** · "
               f"outer_radius_baffle = **{merged['outer_radius_baffle']}**")
    st.caption("\U0001F7E2 green = matched hole radius equals the capsule's own radius · "
               "\U0001F534 red = they differ. Click **Highlight** to point it out on the image.")

    with st.container(height=650):
        for m in merged["matches"]:
            ok = radii_match(m["capsule_radius"], m["nearest_hole_radius"])
            bg = "#123d1f" if ok else "#3d1212"
            border = "#2fae5b" if ok else "#d64545"
            badge = "\U0001F7E2 MATCH" if ok else "\U0001F534 MISMATCH"

            with st.container(border=True):
                st.markdown(
                    f"""
                    <div style="background-color:{bg};border-left:6px solid {border};
                                padding:10px 14px;border-radius:6px;">
                      <b style="color:{border};">{badge}</b>
                      &nbsp;&mdash;&nbsp;<b>Capsule #{m['capsule_index']}</b>
                      <br>
                      <span style="color:#ddd;">
                        capsule_radius = <b>{m['capsule_radius']}</b>
                        &nbsp;vs&nbsp;
                        nearest_hole_radius = <b>{m['nearest_hole_radius']}</b>
                      </span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("Highlight in image", key=f"highlight_{m['capsule_index']}"):
                    st.session_state["selected_capsule_index"] = m["capsule_index"]
                    st.rerun()

    with st.expander("Raw merged JSON"):
        st.json(merged)
