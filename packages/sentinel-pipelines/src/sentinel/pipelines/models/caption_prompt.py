"""Few-shot examples for the vehicle XML contract.

The examples describe visible details in text; only the final user turn
contains an image. They teach the output format without adding example images
to every inference request.
"""

from __future__ import annotations

XML_INSTRUCTIONS = """Describe the vehicle in the supplied crop using only visible evidence.
Return exactly one XML <image> element, with these children in this order:
<thinking>, <type>, <colour>, <make>, <model>, <features>, <caption>.
Use <thinking> for an explanation of visible evidence and uncertainty.
It may span multiple paragraphs; separate them with blank lines inside <thinking>.
Use <type> for car, motorcycle, autorickshaw, bus, truck, bicycle, van, or other.
The detector class is a hint and may be wrong; the image takes precedence.
Leave uncertain fields empty, especially make and model when identifying details
are unreadable. Do not infer make or model from colour alone.
Use lowercase attribute values. Put each distinctive visible feature in its own
<feature> child inside <features>; use <features/> when there are none.
Write a brief factual caption without speculative identities or attributes.
Escape XML text such as & as &amp;. Return no markdown, JSON, or text outside <image>.
The following three text examples demonstrate the format and handling of uncertainty.
"""

FEW_SHOT_EXAMPLES = (
    (
        "Visible details: a white passenger car with clearly readable Tata and Nexon "
        "badges and a roof rack.",
        """<image>
<thinking>The white body, readable badges, and roof rack are visible.</thinking>
<type>car</type>
<colour>white</colour>
<make>tata</make>
<model>nexon</model>
<features><feature>roof rack</feature></features>
<caption>A white Tata Nexon car with a roof rack.</caption>
</image>""",
    ),
    (
        "Visible details: a red motorcycle viewed from the side; its brand and model "
        "markings are too blurred to read. No distinctive accessories are visible.",
        """<image>
<thinking>The red body and motorcycle silhouette are visible from the side.

The brand and model markings are blurred, so those fields remain empty.
No distinctive accessories are visible.</thinking>
<type>motorcycle</type>
<colour>red</colour>
<make/>
<model/>
<features/>
<caption>A red motorcycle viewed from the side.</caption>
</image>""",
    ),
    (
        "Visible details: a three-wheeled passenger autorickshaw with a green body "
        "and a yellow canopy; no manufacturer or model badge can be read.",
        """<image>
<thinking>The three-wheel passenger body and yellow canopy are visible.</thinking>
<type>autorickshaw</type>
<colour>green</colour>
<make/>
<model/>
<features><feature>yellow canopy</feature></features>
<caption>A green autorickshaw with a yellow canopy.</caption>
</image>""",
    ),
)


def caption_messages(image_data_url: str, vehicle_class: str) -> list[dict]:
    # Keep alternating user/assistant turns for models with strict chat templates.
    messages = []
    for index, (observation, answer) in enumerate(FEW_SHOT_EXAMPLES):
        prompt = f"Format example {index + 1}. {observation}"
        if index == 0:
            prompt = XML_INSTRUCTIONS + "\n" + prompt
        messages.extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]
        )
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Now describe this image using the same XML format. "
                        f"Detector class hint: {vehicle_class}. "
                        "Use only this image's visible details, not the example vehicles."
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    )
    return messages
