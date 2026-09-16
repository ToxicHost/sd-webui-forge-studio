import torch

from modules import shared

# `ui_gradio_extensions` is imported inside `webpath()` rather than here.
#
# `ui_gradio_extensions.py:3` is `import gradio as gr`, and this module is on
# the generation path -- `modules/processing.py:30` imports `profiling`. So a
# module-scope import here put Gradio behind every generation:
#
#     modules.processing -> modules.profiling -> modules.ui_gradio_extensions
#                        -> gradio  (119 modules)
#
# The irony is that the borrowed function needs none of it.
# `ui_gradio_extensions.webpath()` is a pure string formatter over
# `util.truncate_path` and `os.path.getmtime`; it only happens to live in a
# module that imports Gradio for its other contents. One call site, one
# function, no behaviour to preserve beyond the string it returns.


class Profiler:
    def __init__(self):
        if not shared.opts.profiling_enable:
            self.profiler = None
            return

        activities = []
        if "CPU" in shared.opts.profiling_activities:
            activities.append(torch.profiler.ProfilerActivity.CPU)
        if "CUDA" in shared.opts.profiling_activities:
            activities.append(torch.profiler.ProfilerActivity.CUDA)

        if not activities:
            self.profiler = None
            return

        self.profiler = torch.profiler.profile(
            activities=activities,
            record_shapes=shared.opts.profiling_record_shapes,
            profile_memory=shared.opts.profiling_profile_memory,
            with_stack=shared.opts.profiling_with_stack,
        )

    def __enter__(self):
        if self.profiler:
            self.profiler.__enter__()

        return self

    def __exit__(self, exc_type, exc, exc_tb):
        if self.profiler:
            shared.state.textinfo = "Finishing profile..."

            self.profiler.__exit__(exc_type, exc, exc_tb)

            self.profiler.export_chrome_trace(shared.opts.profiling_filename)


def webpath():
    from modules import ui_gradio_extensions

    return ui_gradio_extensions.webpath(shared.opts.profiling_filename)
