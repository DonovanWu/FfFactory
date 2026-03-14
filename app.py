import gradio as gr
import subprocess
import tempfile
import os
import functools
from collections import defaultdict

# =====================================================================
# FFmpeg Parameterization (Tweak these to optimize your conversions)
# =====================================================================

# Default arguments for video conversion (e.g., standardizing to H.264)
# You can map these by output extension if you want more granular control.
ARGS_VIDEO_CONVERT = ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k"]

# Default arguments for audio conversion (e.g., standardizing mp3 quality)
ARGS_AUDIO_CONVERT = ["-c:a", "libmp3lame", "-q:a", "2"]

# Default arguments for image conversion
ARGS_IMAGE_CONVERT = ["-q:v", "2"]

# High-quality GIF conversion arguments (using palettegen/paletteuse)
# We handle the complex filter programmatically, but you can add framerate limits here.
ARGS_VIDEO_TO_GIF = ["-r", "15"] # Set framerate to 15 fps for GIF

# Audio extraction args (usually just copy or encode to mp3/wav)
ARGS_EXTRACT_AUDIO = ["-c:a", "libmp3lame", "-q:a", "2", "-vn"] # -vn removes video stream


# =====================================================================
# Core FFmpeg Processing Functions
# =====================================================================

def get_duration(file_path):
    """Uses ffprobe to get the duration of media in seconds."""
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def run_ffmpeg_with_progress(cmd, expected_duration, progress_tracker):
    """Executes FFmpeg and updates the Gradio progress bar."""
    
    # We use `-progress pipe:1` to send progress stats to stdout, 
    # and `-loglevel error` to keep standard logs in stderr.
    full_cmd = ["ffmpeg", "-y"] + cmd + ["-progress", "pipe:1", "-nostats", "-loglevel", "error"]
    
    process = subprocess.Popen(
        full_cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        text=True, 
        universal_newlines=True
    )
    
    # Read stdout line-by-line to parse progress
    for line in process.stdout:
        if "out_time_us=" in line:
            try:
                time_us = int(line.split("=")[1].strip())
                if expected_duration > 0:
                    current_time_sec = time_us / 1_000_000
                    pct = min(current_time_sec / expected_duration, 1.0)
                    progress_tracker(pct, desc="Processing media...")
            except ValueError:
                pass
                
    process.wait()
    
    if process.returncode != 0:
        error_log = process.stderr.read()
        raise gr.Error(f"FFmpeg Error: {error_log}")


def build_base_cmd(input_path, start, end, crop_w, crop_h, crop_x, crop_y, res_w, res_h):
    """Constructs the base ffmpeg command with trimming and visual filters."""
    cmd = []
    
    # Temporal Trimming (Input seeking for speed)
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", input_path]
    if end:
        # If input seeking is used, -to acts from the 0 timestamp of the output
        duration = end - (start if start else 0)
        cmd += ["-t", str(duration)]

    # Visual Filters
    filters = []
    if crop_w and crop_h:
        cx, cy = crop_x or 0, crop_y or 0
        filters.append(f"crop={crop_w}:{crop_h}:{cx}:{cy}")
    if res_w and res_h:
        filters.append(f"scale={res_w}:{res_h}")
        
    return cmd, filters


def calculate_expected_duration(input_path, start, end):
    total = get_duration(input_path)
    if start and end:
        return end - start
    elif start:
        return total - start
    elif end:
        return end
    return total


file_tracker = defaultdict(dict)


def cleanup_last_file(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        req = kwargs.get('request', args[-1])
        if not isinstance(req, gr.Request):
            raise RuntimeError('Failed to get gradio request!')
        cleanup_file(req, func.__name__)
        out_path = func(*args, **kwargs)
        file_tracker[req.session_hash][func.__name__] = out_path
        return out_path
    return wrapper


def cleanup_file(req: gr.Request, func_name: str | None = None):
    request_file = file_tracker.get(req.session_hash, {})
    if func_name is None:
        funcs = list(request_file)
    else:
        funcs = [func_name]

    for name in funcs:
        file_path = request_file.pop(name, None)
        if file_path:
            os.remove(file_path)
            print(f'Removed: {file_path}')

    if func_name is None and req.session_hash in file_tracker and not file_tracker[req.session_hash]:
        # do this only when unloading though it's not a huge impact if not
        del file_tracker[req.session_hash]
        print(f'Cleaned up all files for session: {req.session_hash}')


def get_tempfile_prefix(file_path):
    return os.path.basename(file_path).rsplit('.', maxsplit=1)[0] + '_'


# =====================================================================
# Gradio Endpoint Functions
# =====================================================================

@cleanup_last_file
def convert_video(input_file, sub_file, out_ext, start, end, res_w, res_h, crop_w, crop_h, crop_x, crop_y, progress=gr.Progress(), request: gr.Request = None):
    if not input_file: raise gr.Error("Please upload a file.")
    
    expected_duration = calculate_expected_duration(input_file, start, end)
    _, out_path = tempfile.mkstemp(prefix=get_tempfile_prefix(input_file), suffix=f".{out_ext}")
    
    cmd, filters = build_base_cmd(input_file, start, end, crop_w, crop_h, crop_x, crop_y, res_w, res_h)
    
    # --- NEW: Add Subtitles Filter ---
    if sub_file:
        # We must escape backslashes and colons so FFmpeg's filter parser doesn't crash
        safe_sub_path = sub_file.replace('\\', '/').replace(':', '\\:')
        filters.append(f"subtitles='{safe_sub_path}'")
    # ---------------------------------
    
    if filters:
        cmd += ["-vf", ",".join(filters)]
        
    cmd += ARGS_VIDEO_CONVERT
    cmd += [out_path]
    
    run_ffmpeg_with_progress(cmd, expected_duration, progress)
    return out_path


@cleanup_last_file
def convert_audio(input_file, out_ext, start, end, progress=gr.Progress(), request: gr.Request = None):
    if not input_file: raise gr.Error("Please upload a file.")
    
    expected_duration = calculate_expected_duration(input_file, start, end)
    _, out_path = tempfile.mkstemp(prefix=get_tempfile_prefix(input_file), suffix=f".{out_ext}")
    
    cmd, _ = build_base_cmd(input_file, start, end, None, None, None, None, None, None)
    cmd += ARGS_AUDIO_CONVERT
    cmd += [out_path]
    
    run_ffmpeg_with_progress(cmd, expected_duration, progress)
    return out_path


@cleanup_last_file
def convert_image(input_file, out_ext, res_w, res_h, crop_w, crop_h, crop_x, crop_y, progress=gr.Progress(), request: gr.Request = None):
    if not input_file: raise gr.Error("Please upload a file.")
    
    _, out_path = tempfile.mkstemp(prefix=get_tempfile_prefix(input_file), suffix=f".{out_ext}")
    cmd, filters = build_base_cmd(input_file, None, None, crop_w, crop_h, crop_x, crop_y, res_w, res_h)
    
    if filters:
        cmd += ["-vf", ",".join(filters)]
        
    cmd += ARGS_IMAGE_CONVERT
    cmd += [out_path]
    
    run_ffmpeg_with_progress(cmd, 0, progress) # Images have 0 duration, bar will just show processing
    return out_path


@cleanup_last_file
def convert_to_gif(input_file, start, end, res_w, res_h, crop_w, crop_h, crop_x, crop_y, progress=gr.Progress(), request: gr.Request = None):
    if not input_file: raise gr.Error("Please upload a file.")
    
    expected_duration = calculate_expected_duration(input_file, start, end)
    _, out_path = tempfile.mkstemp(prefix=get_tempfile_prefix(input_file), suffix=".gif")
    
    cmd, filters = build_base_cmd(input_file, start, end, crop_w, crop_h, crop_x, crop_y, res_w, res_h)
    
    # High quality GIF requires a palette filter complex
    filter_str = ",".join(filters) + "," if filters else ""
    complex_filter = f"[0:v] {filter_str}split [a][b];[a] palettegen [p];[b][p] paletteuse"
    
    cmd += ["-filter_complex", complex_filter]
    cmd += ARGS_VIDEO_TO_GIF
    cmd += [out_path]
    
    run_ffmpeg_with_progress(cmd, expected_duration, progress)
    return out_path


@cleanup_last_file
def extract_audio(input_file, out_ext, start, end, progress=gr.Progress(), request: gr.Request = None):
    if not input_file: raise gr.Error("Please upload a file.")
    
    expected_duration = calculate_expected_duration(input_file, start, end)
    _, out_path = tempfile.mkstemp(prefix=get_tempfile_prefix(input_file), suffix=f".{out_ext}")
    
    cmd, _ = build_base_cmd(input_file, start, end, None, None, None, None, None, None)
    cmd += ARGS_EXTRACT_AUDIO
    cmd += [out_path]
    
    run_ffmpeg_with_progress(cmd, expected_duration, progress)
    return out_path


# =====================================================================
# Gradio UI Layout
# =====================================================================

def ui_trim():
    with gr.Row():
        start = gr.Number(label="Start Time (s)", precision=2)
        end = gr.Number(label="End Time (s)", precision=2)
    return start, end


def ui_visuals():
    with gr.Accordion("Resize & Crop (Leave blank to keep original)", open=False):
        with gr.Row():
            res_w = gr.Number(label="Resize Width", precision=0)
            res_h = gr.Number(label="Resize Height", precision=0)
        with gr.Row():
            crop_w = gr.Number(label="Crop Width", precision=0)
            crop_h = gr.Number(label="Crop Height", precision=0)
            crop_x = gr.Number(label="Crop X Offset", precision=0)
            crop_y = gr.Number(label="Crop Y Offset", precision=0)
    return res_w, res_h, crop_w, crop_h, crop_x, crop_y


with gr.Blocks(title="Web FormatFactory") as app:
    gr.Markdown("# Web FormatFactory (Powered by FFmpeg)")
    
    with gr.Tabs():
        # --- VIDEO TO VIDEO ---
        with gr.Tab("Video Converter"):
            with gr.Row():
                with gr.Column():
                    v_in = gr.Video(label="Input Video")
                    
                    # NEW: Subtitle File Uploader
                    v_sub = gr.File(label="Upload Subtitles (Optional, .srt, .ass)", file_types=[".srt", ".ass"]) 
                    
                    v_ext = gr.Dropdown(choices=["mp4", "mkv", "avi", "mov", "webm"], value="mp4", label="Output Format")
                    v_start, v_end = ui_trim()
                    v_rw, v_rh, v_cw, v_ch, v_cx, v_cy = ui_visuals()
                    v_btn = gr.Button("Convert Video", variant="primary")
                with gr.Column():
                    v_out = gr.File(label="Output Video")
            
            # NEW: Add v_sub to the inputs list right after v_in
            v_btn.click(
                convert_video, 
                inputs=[v_in, v_sub, v_ext, v_start, v_end, v_rw, v_rh, v_cw, v_ch, v_cx, v_cy], 
                outputs=v_out
            )

        # --- AUDIO TO AUDIO ---
        with gr.Tab("Audio Converter"):
            with gr.Row():
                with gr.Column():
                    a_in = gr.Audio(type="filepath", label="Input Audio")
                    a_ext = gr.Dropdown(choices=["mp3", "wav", "flac", "aac", "ogg"], value="mp3", label="Output Format")
                    a_start, a_end = ui_trim()
                    a_btn = gr.Button("Convert Audio", variant="primary")
                with gr.Column():
                    a_out = gr.File(label="Output Audio")
                    
            a_btn.click(convert_audio, inputs=[a_in, a_ext, a_start, a_end], outputs=a_out)

        # --- IMAGE TO IMAGE ---
        with gr.Tab("Image Converter"):
            with gr.Row():
                with gr.Column():
                    i_in = gr.Image(type="filepath", label="Input Image")
                    i_ext = gr.Dropdown(choices=["jpg", "png", "webp", "tiff", "bmp"], value="jpg", label="Output Format")
                    i_rw, i_rh, i_cw, i_ch, i_cx, i_cy = ui_visuals()
                    i_btn = gr.Button("Convert Image", variant="primary")
                with gr.Column():
                    i_out = gr.File(label="Output Image")
                    
            i_btn.click(convert_image, inputs=[i_in, i_ext, i_rw, i_rh, i_cw, i_ch, i_cx, i_cy], outputs=i_out)

        # --- VIDEO TO GIF ---
        with gr.Tab("Video to GIF"):
            with gr.Row():
                with gr.Column():
                    g_in = gr.Video(label="Input Video")
                    g_start, g_end = ui_trim()
                    g_rw, g_rh, g_cw, g_ch, g_cx, g_cy = ui_visuals()
                    g_btn = gr.Button("Create GIF", variant="primary")
                with gr.Column():
                    g_out = gr.Image(label="Output GIF", format='gif')
                    
            g_btn.click(convert_to_gif, inputs=[g_in, g_start, g_end, g_rw, g_rh, g_cw, g_ch, g_cx, g_cy], outputs=g_out)

        # --- EXTRACT AUDIO ---
        with gr.Tab("Extract Audio"):
            with gr.Row():
                with gr.Column():
                    e_in = gr.Video(label="Input Video")
                    e_ext = gr.Dropdown(choices=["mp3", "wav", "flac"], value="mp3", label="Output Format")
                    e_start, e_end = ui_trim()
                    e_btn = gr.Button("Extract Audio", variant="primary")
                with gr.Column():
                    e_out = gr.Audio(label="Output Audio")
                    
            e_btn.click(extract_audio, inputs=[e_in, e_ext, e_start, e_end], outputs=e_out)

    app.unload(cleanup_file)

app.launch(share=False, root_path=os.getenv('GRADIO_ROOT_PATH'))
