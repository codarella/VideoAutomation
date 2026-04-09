"""
tts_client.py — Gemini 2.5 Flash TTS API client.

Sends one text chunk to the Gemini TTS model and returns raw PCM16 bytes.
Retries on transient failures with exponential backoff. Raises TTSError on
permanent failure so the pipeline can skip the chunk and continue.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

logger = logging.getLogger("tts_pipeline")


class TTSError(Exception):
    """Raised when a TTS chunk fails after all retries."""


def synthesize_chunk(
    text: str,
    api_key: str,
    voice_name: str = "Kore",
    emotional_prompt: str = "",
    model: str = "gemini-2.5-flash-preview-tts",
    sample_rate: int = 24000,
    max_retries: int = 3,
) -> bytes:
    """
    Send a single text chunk to the Gemini TTS API.

    Returns raw PCM16 bytes (mono, 24000 Hz by default).
    Raises TTSError if all retries are exhausted.

    Args:
        text:             The narration text to synthesize.
        api_key:          Gemini API key.
        voice_name:       Prebuilt voice name (e.g. "Kore", "Charon").
        emotional_prompt: System instruction that sets tone/style.
        model:            Gemini TTS model ID.
        sample_rate:      Expected sample rate — used only for logging.
        max_retries:      Number of attempts before giving up.

    Returns:
        Raw PCM16 audio bytes.
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        raise TTSError(
            "google-genai package not installed. Run: pip install google-genai>=1.0.0"
        ) from exc

    client = genai.Client(api_key=api_key)

    # Build speech config
    speech_cfg = genai_types.SpeechConfig(
        voice_config=genai_types.VoiceConfig(
            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                voice_name=voice_name
            )
        )
    )

    # Build generate config
    gen_config_kwargs: dict = {
        "response_modalities": ["AUDIO"],
        "speech_config": speech_cfg,
    }
    if emotional_prompt.strip():
        gen_config_kwargs["system_instruction"] = emotional_prompt.strip()

    gen_config = genai_types.GenerateContentConfig(**gen_config_kwargs)

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("TTS attempt %d/%d — %d chars", attempt, max_retries, len(text))
            response = client.models.generate_content(
                model=model,
                contents=text,
                config=gen_config,
            )

            # Extract audio bytes from response
            candidates = getattr(response, "candidates", None)
            if not candidates:
                raise TTSError("Gemini TTS returned no candidates")

            parts = getattr(candidates[0].content, "parts", None)
            if not parts:
                raise TTSError("Gemini TTS candidate has no parts")

            inline = getattr(parts[0], "inline_data", None)
            if inline is None:
                raise TTSError("Gemini TTS part has no inline_data")

            audio_bytes = inline.data
            if not audio_bytes:
                raise TTSError("Gemini TTS returned empty audio data")

            logger.debug(
                "TTS OK — received %d bytes (attempt %d)", len(audio_bytes), attempt
            )
            return audio_bytes

        except TTSError:
            raise  # permanent structural errors — don't retry
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(
                    "TTS attempt %d failed (%s). Retrying in %ds...", attempt, exc, wait
                )
                time.sleep(wait)
            else:
                logger.error(
                    "TTS failed after %d attempts: %s", max_retries, exc
                )

    raise TTSError(f"All {max_retries} TTS attempts failed") from last_exc


def synthesize_chunk_vertex(
    text: str,
    gcp_project: str,
    gcp_location: str = "us-central1",
    voice_name: str = "Kore",
    emotional_prompt: str = "",
    model: str = "gemini-2.5-flash-preview-tts",
    sample_rate: int = 24000,
    max_retries: int = 3,
) -> bytes:
    """
    Send a single text chunk to the Gemini TTS API via Vertex AI.

    Uses Google Application Default Credentials (ADC) — no API key needed.
    Run `gcloud auth application-default login` once to set up credentials,
    or set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON path.

    Args:
        text:             The narration text to synthesize.
        gcp_project:      Google Cloud project ID.
        gcp_location:     GCP region (default: us-central1).
        voice_name:       Prebuilt voice name (e.g. "Kore", "Charon").
        emotional_prompt: System instruction that sets tone/style.
        model:            Gemini TTS model ID.
        sample_rate:      Expected sample rate — used only for logging.
        max_retries:      Number of attempts before giving up.

    Returns:
        Raw PCM16 audio bytes.
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        raise TTSError(
            "google-genai package not installed. Run: pip install google-genai>=1.0.0"
        ) from exc

    if not gcp_project:
        raise TTSError(
            "gcp_project is required for Vertex AI. Set it in tts_config.json or "
            "the GOOGLE_CLOUD_PROJECT environment variable."
        )

    # Vertex AI client — auth via Application Default Credentials
    client = genai.Client(
        vertexai=True,
        project=gcp_project,
        location=gcp_location,
    )

    speech_cfg = genai_types.SpeechConfig(
        voice_config=genai_types.VoiceConfig(
            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                voice_name=voice_name
            )
        )
    )

    gen_config = genai_types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=speech_cfg,
    )

    # Vertex AI TTS does not support system_instruction — embed the prompt
    # directly into the text content instead
    tts_input = text
    if emotional_prompt.strip():
        tts_input = f"{emotional_prompt.strip()}\n\n{text}"

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(
                "TTS (Vertex) attempt %d/%d — %d chars", attempt, max_retries, len(tts_input)
            )
            response = client.models.generate_content(
                model=model,
                contents=tts_input,
                config=gen_config,
            )

            candidates = getattr(response, "candidates", None)
            if not candidates:
                raise TTSError("Gemini TTS (Vertex) returned no candidates")

            parts = getattr(candidates[0].content, "parts", None)
            if not parts:
                raise TTSError("Gemini TTS (Vertex) candidate has no parts")

            inline = getattr(parts[0], "inline_data", None)
            if inline is None:
                raise TTSError("Gemini TTS (Vertex) part has no inline_data")

            audio_bytes = inline.data
            if not audio_bytes:
                raise TTSError("Gemini TTS (Vertex) returned empty audio data")

            logger.debug(
                "TTS (Vertex) OK — received %d bytes (attempt %d)",
                len(audio_bytes), attempt,
            )
            return audio_bytes

        except TTSError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "TTS (Vertex) attempt %d failed (%s). Retrying in %ds...",
                    attempt, exc, wait,
                )
                import time
                time.sleep(wait)
            else:
                logger.error(
                    "TTS (Vertex) failed after %d attempts: %s", max_retries, exc
                )

    raise TTSError(f"All {max_retries} Vertex TTS attempts failed") from last_exc


def synthesize_chunk_cloud_tts(
    text: str,
    voice_name: str = "en-US-Chirp-HD-D",
    language_code: str = "en-US",
    sample_rate: int = 24000,
    max_retries: int = 3,
) -> bytes:
    """
    Send a single text chunk to the Google Cloud Text-to-Speech API (Chirp HD).

    Uses Application Default Credentials — no API key needed. Auth is handled
    by the gcloud credentials already set up via `gcloud auth application-default login`.

    The emotional prompt is not supported by Cloud TTS — voice character and
    quality come from the selected Chirp HD voice itself.

    Free tier: 1,000,000 characters per month at no cost.

    Args:
        text:          The narration text to synthesize.
        voice_name:    Cloud TTS voice name (e.g. "en-US-Chirp-HD-D").
                       Full list: https://cloud.google.com/text-to-speech/docs/voices
        language_code: BCP-47 language code (e.g. "en-US").
        sample_rate:   Output sample rate in Hz.
        max_retries:   Number of attempts before giving up.

    Returns:
        WAV file bytes (complete file with header, ready for pydub).
    """
    try:
        from google.cloud import texttospeech
    except ImportError as exc:
        raise TTSError(
            "google-cloud-texttospeech not installed. "
            "Run: pip install google-cloud-texttospeech"
        ) from exc

    client = texttospeech.TextToSpeechClient()

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice_params = texttospeech.VoiceSelectionParams(
        language_code=language_code,
        name=voice_name,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,
    )

    last_exc: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(
                "TTS (Cloud) attempt %d/%d — %d chars", attempt, max_retries, len(text)
            )
            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            audio_bytes = response.audio_content
            if not audio_bytes:
                raise TTSError("Cloud TTS returned empty audio")

            logger.debug(
                "TTS (Cloud) OK — %d bytes (attempt %d)", len(audio_bytes), attempt
            )
            return audio_bytes  # complete WAV file bytes

        except TTSError:
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "TTS (Cloud) attempt %d failed (%s). Retrying in %ds...",
                    attempt, exc, wait,
                )
                import time
                time.sleep(wait)
            else:
                logger.error(
                    "TTS (Cloud) failed after %d attempts: %s", max_retries, exc
                )

    raise TTSError(f"All {max_retries} Cloud TTS attempts failed") from last_exc
