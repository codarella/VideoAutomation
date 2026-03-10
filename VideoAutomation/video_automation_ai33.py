#!/usr/bin/env python3
"""
YouTube Video Automation System with AI33.pro (Nano Banans Model)
==================================================================

Production-ready system for automating YouTube video creation:
- Auto-transcribes audio using Google Speech Recognition
- Generates AI images using AI33.pro with Nano Banans model
- Always sends style reference image for consistency
- Syncs images with audio timing
- Supports custom character overlay (~35% of scenes)
- Compiles final video with FFmpeg

For Windows with your directory structure:
C:\\VideoAutomation\\
├── video_workspace\\
│   ├── audio\\
│   ├── characters\\
│   ├── images\\
│   ├── intro\\
│   ├── scripts\\
│   ├── style_references\\
│   └── videos\\

Usage:
    python video_automation_ai33.py ^
        --audio "video_workspace\\audio\\test_audio.mp3" ^
        --name "my_video" ^
        --ai33-key "YOUR_API_KEY" ^
        --character-rate 0.35 ^
        --workers 5

Version: 2.0.0 (Nano Banans + Reference Images)
"""

import os
import sys
import json
import time
import random
import argparse
import tempfile
import subprocess
import shutil
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Third-party imports
try:
    import requests
    from PIL import Image, ImageDraw, ImageFont
    from pydub import AudioSegment
    import speech_recognition as sr
except ImportError as e:
    print(f"Missing required package: {e}")
    print("\nInstall required packages with:")
    print("pip install requests pillow pydub SpeechRecognition")
    sys.exit(1)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Global configuration for video automation."""
    # API Settings
    ai33_base_url: str = "https://api.ai33.pro"
    ai33_model: str = "bytedance-seedream-4.5"  # Cheaper than Gemini (~243 credits vs 2400)
    
    # Claude API for smart prompts
    claude_api_key: str = ""
    claude_model: str = "claude-3-5-sonnet-20241022"  # Latest stable Sonnet
    use_claude_prompts: bool = False
    
    # Image Generation
    aspect_ratio: str = "16:9"
    resolution: str = "2K"  # bytedance only supports 2K or 4K
    image_width: int = 1920
    image_height: int = 1080
    
    # Video Settings
    fps: int = 30
    seconds_per_image: float = 6.0
    
    # Character Settings - REDUCED TO 20%
    default_character_rate: float = 0.20  # Only 20% of scenes
    character_min_size: float = 0.20
    character_max_size: float = 0.30
    
    # API Polling
    poll_interval: float = 4.0
    max_poll_time: float = 300.0
    
    # Costs
    credits_per_image: int = 243
    
    # Retry Settings
    max_retries: int = 3
    retry_delay: float = 5.0
    
    # Visual Style
    color_palette: List[str] = field(default_factory=lambda: [
        "#0a0a1a", "#1a0a2e", "#00d4ff", "#b15573", "#ff9f43", "#6c5ce7",
    ])
    
    # Scene Types
    scene_types: List[str] = field(default_factory=lambda: [
        "intro", "explaining", "reaction", "transition", 
        "pure_concept", "data_visualization", "number_display", "conclusion"
    ])


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class Scene:
    """Represents a single scene in the video."""
    index: int
    text: str
    start_time: float
    end_time: float
    duration: float
    scene_type: str
    include_character: bool
    image_path: Optional[str] = None
    prompt: Optional[str] = None
    generation_status: str = "pending"


@dataclass
class TranscriptSegment:
    """A segment from audio transcription."""
    text: str
    start_time: float
    end_time: float


@dataclass 
class GenerationResult:
    """Result of image generation."""
    scene_index: int
    success: bool
    image_path: Optional[str] = None
    error: Optional[str] = None
    credits_used: int = 0
    generation_time: float = 0.0


# =============================================================================
# PROGRESS TRACKER
# =============================================================================

class ProgressTracker:
    """Thread-safe progress tracking with pretty output."""
    
    def __init__(self, total: int, description: str = "Progress"):
        self.total = total
        self.current = 0
        self.description = description
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.errors = 0
        self.credits_used = 0
    
    def update(self, amount: int = 1, credits: int = 0, error: bool = False):
        with self.lock:
            self.current += amount
            self.credits_used += credits
            if error:
                self.errors += 1
            self._print_progress()
    
    def _print_progress(self):
        percent = (self.current / self.total) * 100
        filled = int(percent / 2)
        bar = "█" * filled + "░" * (50 - filled)
        elapsed = time.time() - self.start_time
        
        if self.current > 0:
            eta = (elapsed / self.current) * (self.total - self.current)
            eta_str = f"ETA: {int(eta)}s"
        else:
            eta_str = "ETA: --"
        
        print(f"\r{self.description}: |{bar}| {percent:.1f}% "
              f"({self.current}/{self.total}) {eta_str} "
              f"Credits: {self.credits_used:,} Errors: {self.errors}  ", end="", flush=True)
    
    def finish(self):
        print()


# =============================================================================
# CLAUDE API PROMPT GENERATOR
# =============================================================================

class ClaudePromptGenerator:
    """Uses Claude API to generate intelligent, relevant image prompts."""
    
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.total_input_tokens = 0
        self.total_output_tokens = 0
    
    def generate_prompt(
        self, 
        script_text: str, 
        scene_type: str, 
        video_title: str,
        include_character: bool,
        scene_index: int,
        total_scenes: int
    ) -> str:
        """
        Generate an intelligent image prompt using Claude API.
        
        Args:
            script_text: The transcript text for this scene
            scene_type: Type of scene (intro, explaining, etc.)
            video_title: Title of the video
            include_character: Whether to include character
            scene_index: Current scene number
            total_scenes: Total number of scenes
            
        Returns:
            Generated image prompt
        """
        
        system_prompt = """You are an art director creating image generation prompts for educational YouTube videos about space and science.
The visual style sits between a high-quality children's science encyclopedia (think DK Eyewitness, National Geographic Kids) and a quality children's animated show — slightly whimsical in warmth and energy, but scientifically grounded in what things actually look like.

STYLE RULES:
1. COSMIC OBJECTS look real but illustrated: stars are actual glowing points of light with warmth and soft halos — not clip-art symbols. Black holes have real accretion disks and warped light. Galaxies have actual spiral arms. Nebulae have real cloud-like depth and color. The illustration style is in the warmth and painterly rendering, NOT in replacing real things with simplified icons.
2. ENVIRONMENT: Painterly illustrated space — deep navy/purple skies, softly glowing nebulae with warm color variation, rich starfields with depth. Backgrounds feel like a beautifully illustrated science book page: warm, inviting, slightly magical — not cold and clinical, not a stock photo.
3. CHARACTERS: Simple rounded figures — slightly chubby proportions, large expressive eyes, friendly and warm. Clear body language and emotion. Think quality children's educational TV character energy: approachable but not babyish.
4. PALETTE: Warm and rich — sky blue, warm yellow, soft orange, coral, grass green alongside space purples and navy. Friendly and saturated but harmonious.
5. TONE: The universe feels exciting and slightly magical — a place a curious person would want to explore. Not cold, not scary, not purely clinical.
6. SCALE: Use scale for wonder — a small figure against a vast beautiful cosmos.
7. NO text, labels, dates, or any words in the image
8. NO photorealism, NO 3D rendering, NO anime style
9. Output ONLY the image prompt, nothing else

LITERAL TRANSLATION RULE: The scene text IS the visual. Translate what is described almost directly into an image.
If the script says "a probe drifted for 40 years through empty space" — show a probe drifting through empty space.
If the script says "scientists detected a radio signal from a distant star" — show a signal beam, a real-looking star, scientists at a dish.
Do NOT replace the literal content with a metaphor or a character just thinking about it.

CONTEXT RULE: The literal content tells you WHAT to show. The broader context of the scene tells you HOW it should feel — the mood, the color, the emotional weight of the environment around it.

VISUAL STORYTELLING RULE: Show an ACTION — a verb, not a noun. Freeze-frame the most charged instant.
- BAD: "a black hole" (static noun, no story)
- GOOD: "a small rounded cartoon figure drifting helplessly toward a real illustrated black hole — accretion disk blazing warm amber and white, light visibly bending around the event horizon, the figure's arms spread wide in stunned awe" (literal + action + real cosmic object + warm illustrated style)

PROMPT STRUCTURE: Describe the illustrated environment first (painterly, warm, alive), then the foreground action."""

        user_prompt = f"""Create an illustrated image prompt for scene {scene_index + 1}/{total_scenes} of "{video_title}"

SCRIPT TEXT:
"{script_text[:300]}"

SCENE TYPE: {scene_type}
{"INCLUDE a small expressive character (rounded friendly proportions, large expressive eyes, clear body language) somewhere in the frame" if include_character else "NO character — the cosmic environment or event IS the subject. Make it feel alive, warm, and wondrous."}

Step 1 — LITERAL READ: What objects, events, and actions are literally described in this script text?
Step 2 — CONTEXT & TONE: What is the broader emotional context? What should this moment feel like? (wonder, dread, cold realization, curiosity, urgency?)
Step 3 — ENVIRONMENT: Painterly illustrated space backdrop — what does it look like and feel like given the tone? (colors, light, scale, mood)
Step 4 — FREEZE-FRAME: Show the literal content from Step 1 as an action happening inside the environment from Step 3, colored by the feeling from Step 2.

Requirements:
1. BACKGROUND: painterly illustrated space — warm, inviting, slightly magical, like a beautifully rendered science book page
2. COSMIC OBJECTS must look scientifically real but illustrated — stars glow with warmth, black holes have real structure, galaxies have real form
3. FOREGROUND: rounded friendly cartoon figures/objects showing the LITERAL scene content as an action
4. Palette: sky blue, warm yellow, soft orange, coral, space purple, navy — warm and harmonious
5. NO text, labels, or words anywhere in the image
6. Use scale and composition to reinforce the emotional weight and wonder of the scene"""

        try:
            response = requests.post(
                self.api_url,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": self.model,
                    "max_tokens": 300,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ]
                },
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Track token usage
                usage = data.get('usage', {})
                self.total_input_tokens += usage.get('input_tokens', 0)
                self.total_output_tokens += usage.get('output_tokens', 0)
                
                # Extract the prompt
                content = data.get('content', [])
                if content and content[0].get('type') == 'text':
                    generated = content[0].get('text', '').strip()
                    print(f"      🤖 Claude: Generated prompt ({len(generated)} chars)")
                    return generated
            
            else:
                # Print detailed error for debugging
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', {}).get('message', response.text[:200])
                    print(f"      ⚠ Claude API error {response.status_code}: {error_msg}")
                except:
                    print(f"      ⚠ Claude API error {response.status_code}: {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"      ⚠ Claude API exception: {e}")
            return None
    
    def get_cost_estimate(self) -> dict:
        """Get estimated cost based on token usage."""
        # Claude Sonnet 4 pricing (as of 2024)
        input_cost = (self.total_input_tokens / 1_000_000) * 3.00  # $3/1M input
        output_cost = (self.total_output_tokens / 1_000_000) * 15.00  # $15/1M output
        
        return {
            'input_tokens': self.total_input_tokens,
            'output_tokens': self.total_output_tokens,
            'input_cost': input_cost,
            'output_cost': output_cost,
            'total_cost': input_cost + output_cost
        }


# =============================================================================
# AI33 IMAGE GENERATOR (NANO BANANS + REFERENCE IMAGES)
# =============================================================================

class AI33ImageGenerator:
    """Handles image generation using AI33.pro API with Nano Banans model."""
    
    def __init__(self, api_key: str, config: Config):
        self.api_key = api_key
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "xi-api-key": api_key
        })
    
    def generate_image(
        self, 
        prompt: str, 
        output_path: str,
        character_path: Optional[str] = None,
        style_ref_path: Optional[str] = None
    ) -> GenerationResult:
        """
        Generate an image using AI33.pro API.
        
        COST OPTIMIZATION:
        - No assets = ~243 credits (cheapest)
        - 1 asset = ~700 credits  
        - 2 assets = ~1188 credits (most expensive)
        
        We only send assets when character is needed.
        Style reference is included in the prompt text description instead.
        
        Args:
            prompt: Image description
            output_path: Where to save the generated image
            character_path: Optional path to character image (MC.png) - only sent when needed
            style_ref_path: NOT USED as asset anymore (saves credits)
            
        Returns:
            GenerationResult with success status and details
        """
        start_time = time.time()
        
        # Only add @img1 reference if character is being sent
        if character_path:
            final_prompt = f"@img1 {prompt}"
        else:
            final_prompt = prompt
        
        # Prepare form data
        data = {
            'prompt': final_prompt,
            'model_id': self.config.ai33_model,
            'generations_count': '1',
            'model_parameters': json.dumps({
                'aspect_ratio': self.config.aspect_ratio,
                'resolution': self.config.resolution
            })
        }
        
        # Only send character as asset (style is described in prompt)
        files = []
        file_handles = []
        
        task_id = None
        estimated_credits = self.config.credits_per_image
        
        try:
            # Only send character if provided (20% of scenes)
            if character_path and os.path.exists(character_path):
                fh = open(character_path, 'rb')
                file_handles.append(fh)
                files.append(('assets', (os.path.basename(character_path), fh, 'image/png')))
                print(f"      🎭 Character: {os.path.basename(character_path)} (~700 credits)")
            else:
                print(f"      📷 No assets (~243 credits)")
            
            # Submit generation request with retry
            max_submit_retries = 3
            response = None
            
            for attempt in range(max_submit_retries):
                try:
                    response = self.session.post(
                        f"{self.config.ai33_base_url}/v1i/task/generate-image",
                        data=data,
                        files=files if files else None,
                        timeout=120
                    )
                    break
                except (requests.exceptions.ConnectionError, 
                        requests.exceptions.Timeout,
                        requests.exceptions.ChunkedEncodingError) as e:
                    if attempt < max_submit_retries - 1:
                        print(f"      ⚠ Connection error, retrying ({attempt + 1}/{max_submit_retries})...")
                        time.sleep(5)
                        for fh in file_handles:
                            fh.seek(0)
                    else:
                        return GenerationResult(
                            scene_index=-1,
                            success=False,
                            error=f"Connection failed after {max_submit_retries} attempts: {str(e)}"
                        )
            
            if response is None:
                return GenerationResult(
                    scene_index=-1,
                    success=False,
                    error="No response received"
                )
            
            if response.status_code == 429:
                # Queue full — retry submission with backoff
                max_queue_retries = 6
                for queue_attempt in range(max_queue_retries):
                    wait = 30 * (queue_attempt + 1)
                    print(f"      ⚠ Retry {queue_attempt + 2}/{max_queue_retries + 1}: {response.text[:120].strip()}")
                    print(f"      ⏳ Queue full, waiting {wait}s before retry...")
                    time.sleep(wait)
                    try:
                        for fh in file_handles:
                            fh.seek(0)
                        response = self.session.post(
                            f"{self.config.ai33_base_url}/v1i/task/generate-image",
                            data=data,
                            files=files if files else None,
                            timeout=120
                        )
                    except Exception as e:
                        response = None
                        break
                    if response is not None and response.status_code != 429:
                        break
                else:
                    return GenerationResult(
                        scene_index=-1,
                        success=False,
                        error=f"API 429: queue still full after {max_queue_retries} retries"
                    )
                if response is None:
                    return GenerationResult(
                        scene_index=-1,
                        success=False,
                        error="No response after 429 retry"
                    )

            if response.status_code != 200:
                return GenerationResult(
                    scene_index=-1,
                    success=False,
                    error=f"API error {response.status_code}: {response.text[:200]}"
                )
            
            result = response.json()
            
            if not result.get('success') and not result.get('task_id'):
                return GenerationResult(
                    scene_index=-1,
                    success=False,
                    error=f"Failed to start generation: {result}"
                )
            
            task_id = result.get('task_id')
            if not task_id:
                return GenerationResult(
                    scene_index=-1,
                    success=False,
                    error=f"No task_id in response: {result}"
                )
            
            estimated_credits = result.get('estimated_credits', self.config.credits_per_image)
            remaining = result.get('ec_remain_credits', 'unknown')
            print(f"      ⏳ Task: {task_id[:12]}... Credits: {estimated_credits} (Remaining: {remaining})")
            
        except Exception as e:
            return GenerationResult(
                scene_index=-1,
                success=False,
                error=f"Request error: {str(e)}"
            )
        finally:
            for fh in file_handles:
                try:
                    fh.close()
                except:
                    pass
        
        # Poll for completion
        poll_start = time.time()
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while (time.time() - poll_start) < self.config.max_poll_time:
            time.sleep(self.config.poll_interval)
            
            try:
                status_response = self.session.get(
                    f"{self.config.ai33_base_url}/v1/task/{task_id}",
                    timeout=30
                )
                
                consecutive_errors = 0
                
                if status_response.status_code != 200:
                    continue
                
                status_data = status_response.json()
                status = status_data.get('status', '')
                
                if status == 'done':
                    result_images = status_data.get('metadata', {}).get('result_images', [])
                    if not result_images:
                        return GenerationResult(
                            scene_index=-1,
                            success=False,
                            error="No images in result"
                        )
                    
                    image_url = result_images[0].get('imageUrl')
                    if not image_url:
                        return GenerationResult(
                            scene_index=-1,
                            success=False,
                            error="No image URL in result"
                        )
                    
                    # Download image with retry
                    for dl_attempt in range(3):
                        try:
                            image_response = requests.get(image_url, timeout=60)
                            if image_response.status_code == 200:
                                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                                
                                with open(output_path, 'wb') as f:
                                    f.write(image_response.content)
                                
                                return GenerationResult(
                                    scene_index=-1,
                                    success=True,
                                    image_path=output_path,
                                    credits_used=estimated_credits,
                                    generation_time=time.time() - start_time
                                )
                            else:
                                if dl_attempt < 2:
                                    time.sleep(2)
                                    continue
                                return GenerationResult(
                                    scene_index=-1,
                                    success=False,
                                    error=f"Failed to download image: {image_response.status_code}"
                                )
                        except Exception as e:
                            if dl_attempt < 2:
                                time.sleep(2)
                                continue
                            return GenerationResult(
                                scene_index=-1,
                                success=False,
                                error=f"Download error: {str(e)}"
                            )
                
                elif status == 'error':
                    error_msg = status_data.get('error_message', 'Unknown error')
                    return GenerationResult(
                        scene_index=-1,
                        success=False,
                        error=error_msg
                    )
                
            except requests.exceptions.RequestException as e:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    return GenerationResult(
                        scene_index=-1,
                        success=False,
                        error=f"Too many polling errors: {str(e)}"
                    )
                continue
        
        return GenerationResult(
            scene_index=-1,
            success=False,
            error=f"Generation timed out after {self.config.max_poll_time}s"
        )
    
    def check_credits(self) -> Optional[int]:
        """Check remaining credits."""
        try:
            response = self.session.post(
                f"{self.config.ai33_base_url}/v1i/task/price",
                json={
                    "model_id": self.config.ai33_model,
                    "generations_count": 1,
                    "model_parameters": {
                        "aspect_ratio": self.config.aspect_ratio,
                        "resolution": self.config.resolution
                    }
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('credits', self.config.credits_per_image)
            return None
        except:
            return None


# =============================================================================
# AUDIO TRANSCRIPTION
# =============================================================================

class AudioTranscriber:
    """Handles audio transcription using Google Speech Recognition."""
    
    def __init__(self, config: Config):
        self.config = config
        self.recognizer = sr.Recognizer()
    
    def transcribe(self, audio_path: str) -> List[TranscriptSegment]:
        """Transcribe audio file to text segments."""
        print(f"\n📝 Transcribing audio: {audio_path}")
        
        audio = AudioSegment.from_file(audio_path)
        total_duration = len(audio) / 1000.0
        
        print(f"   Audio duration: {total_duration:.1f} seconds")
        
        chunk_duration = 30000  # 30 seconds
        segments = []
        
        with tempfile.TemporaryDirectory() as temp_dir:
            chunk_index = 0
            for start_ms in range(0, len(audio), chunk_duration):
                end_ms = min(start_ms + chunk_duration, len(audio))
                chunk = audio[start_ms:end_ms]
                
                chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}.wav")
                chunk.export(chunk_path, format="wav")
                
                try:
                    with sr.AudioFile(chunk_path) as source:
                        audio_data = self.recognizer.record(source)
                        text = self.recognizer.recognize_google(audio_data)
                        
                        segments.append(TranscriptSegment(
                            text=text,
                            start_time=start_ms / 1000.0,
                            end_time=end_ms / 1000.0
                        ))
                        
                        print(f"   ✓ Chunk {chunk_index + 1}: {len(text)} chars")
                        
                except sr.UnknownValueError:
                    print(f"   ⚠ Chunk {chunk_index + 1}: Could not understand audio")
                except sr.RequestError as e:
                    print(f"   ⚠ Chunk {chunk_index + 1}: API error - {e}")
                
                chunk_index += 1
        
        print(f"   ✓ Transcribed {len(segments)} segments")
        return segments
    
    def get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds."""
        audio = AudioSegment.from_file(audio_path)
        return len(audio) / 1000.0


# =============================================================================
# SCENE ANALYZER
# =============================================================================

class SceneAnalyzer:
    """Analyzes transcript to create scene breakdowns."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def detect_scene_type(self, text: str, index: int, total: int) -> str:
        """Detect scene type based on text content and position."""
        text_lower = text.lower()
        
        if index == 0:
            return "intro"
        if index >= total - 1:
            return "conclusion"
        
        # Check for "Number X" phrases only (like "Number 9", "Number 10")
        import re
        number_phrase = re.search(r'\bnumber\s+\d+\b', text_lower)
        if number_phrase:
            return "number_display"
        
        if any(word in text_lower for word in ["data", "chart", "graph", "statistics"]):
            return "data_visualization"
        
        if any(word in text_lower for word in ["concept", "theory", "principle", "law", "formula"]):
            return "pure_concept"
        
        if any(word in text_lower for word in ["wow", "amazing", "incredible", "surprising", "shocking"]):
            return "reaction"
        
        if any(word in text_lower for word in ["so", "therefore", "thus", "moving on", "next"]):
            return "transition"
        
        return "explaining"
    
    def should_include_character(self, scene_type: str, character_rate: float) -> bool:
        """Determine if character should appear in this scene - purely random based on rate."""
        # Pure random - character appears in X% of ALL scenes regardless of type
        # No special treatment for intro/conclusion
        if scene_type in ["pure_concept", "data_visualization", "number_display"]:
            return False  # Never on these types
        
        # Random based on rate (default 20%)
        return random.random() < character_rate
    
    def create_scenes(
        self, 
        segments: List[TranscriptSegment],
        audio_duration: float,
        character_rate: float,
        video_title: str = ""
    ) -> List[Scene]:
        """Create scene breakdown from transcript segments."""
        num_scenes = max(1, int(audio_duration / self.config.seconds_per_image))
        scene_duration = audio_duration / num_scenes
        
        print(f"\n🎬 Creating {num_scenes} scenes ({scene_duration:.1f}s each)")
        
        full_text = " ".join([seg.text for seg in segments])
        words = full_text.split()
        words_per_scene = max(1, len(words) // num_scenes)
        
        scenes = []
        for i in range(num_scenes):
            start_word = i * words_per_scene
            end_word = start_word + words_per_scene if i < num_scenes - 1 else len(words)
            scene_text = " ".join(words[start_word:end_word])
            
            if not scene_text.strip():
                scene_text = f"Scene {i + 1} of {video_title}" if video_title else f"Scene {i + 1}"
            
            scene_type = self.detect_scene_type(scene_text, i, num_scenes)
            include_char = self.should_include_character(scene_type, character_rate)
            
            scenes.append(Scene(
                index=i,
                text=scene_text,
                start_time=i * scene_duration,
                end_time=(i + 1) * scene_duration,
                duration=scene_duration,
                scene_type=scene_type,
                include_character=include_char
            ))
        
        char_count = sum(1 for s in scenes if s.include_character)
        print(f"   Character appears in {char_count}/{num_scenes} scenes ({100*char_count/num_scenes:.1f}%)")
        
        return scenes


# =============================================================================
# PROMPT BUILDER
# =============================================================================

class PromptBuilder:
    """Builds image generation prompts for scenes."""
    
    def __init__(self, config: Config, video_title: str = "", claude_generator: Optional[ClaudePromptGenerator] = None):
        self.config = config
        self.video_title = video_title
        self.claude_generator = claude_generator
        self.total_scenes = 0  # Set by caller
    
    def extract_key_number(self, text: str) -> str:
        """Extract 'Number X' from text."""
        import re
        
        # Only match "Number X" pattern
        match = re.search(r'\b(number\s+\d+)\b', text, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return ""
    
    def extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords from text for image relevance."""
        # Common words to ignore
        stopwords = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 
                     'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                     'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                     'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'in',
                     'for', 'on', 'with', 'at', 'by', 'from', 'as', 'into',
                     'through', 'during', 'before', 'after', 'above', 'below',
                     'between', 'under', 'again', 'further', 'then', 'once',
                     'here', 'there', 'when', 'where', 'why', 'how', 'all',
                     'each', 'few', 'more', 'most', 'other', 'some', 'such',
                     'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than',
                     'too', 'very', 'just', 'and', 'but', 'if', 'or', 'because',
                     'until', 'while', 'this', 'that', 'these', 'those', 'it'}
        
        # Extract words
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
        
        # Filter and get unique keywords
        keywords = []
        seen = set()
        for word in words:
            if word not in stopwords and word not in seen:
                keywords.append(word)
                seen.add(word)
                if len(keywords) >= 5:  # Max 5 keywords
                    break
        
        return keywords
    
    def generate_segment_title(self, text: str, scene_type: str) -> str:
        """Generate a short title for the segment based on content."""
        keywords = self.extract_keywords(text)
        
        if scene_type == "intro":
            return self.video_title or "Introduction"
        elif scene_type == "conclusion":
            return "Conclusion" if not keywords else f"Summary: {keywords[0].title()}"
        elif keywords:
            # Create title from top keywords
            return " ".join(word.title() for word in keywords[:3])
        else:
            return "Exploring the Concept"
    
    def build_prompt(self, scene: Scene) -> str:
        """Build image generation prompt for a scene."""
        
        # Try Claude API first if available
        if self.claude_generator:
            claude_prompt = self.claude_generator.generate_prompt(
                script_text=scene.text,
                scene_type=scene.scene_type,
                video_title=self.video_title,
                include_character=scene.include_character,
                scene_index=scene.index,
                total_scenes=self.total_scenes
            )
            if claude_prompt:
                # Only add @img1 if character is included (character becomes @img1)
                if scene.include_character:
                    return f"@img1 {claude_prompt}"
                else:
                    return claude_prompt
        
        # Fallback to rule-based prompt generation
        return self._build_fallback_prompt(scene)
    
    def _build_fallback_prompt(self, scene: Scene) -> str:
        """Build prompt without Claude API (fallback)."""
        
        # SPECIAL CASE: Number display - white background with just the number
        if scene.scene_type == "number_display":
            key_number = self.extract_key_number(scene.text)
            return f"""
            Clean minimalist image with pure white background.
            Display only this text/number prominently in the center: "{key_number}"
            Large, bold, modern sans-serif font.
            Black or dark gray text on white background.
            No other elements, no decorations, no characters.
            Simple, clean, professional infographic style.
            16:9 aspect ratio, high contrast.
            """
        
        # Extract keywords and generate segment title
        keywords = self.extract_keywords(scene.text)
        segment_title = self.generate_segment_title(scene.text, scene.scene_type)
        
        # Simple visual based on keywords
        if keywords:
            main_visual = keywords[0]
        else:
            main_visual = "abstract concept"
        
        # Title bar instruction - white bar at TOP only
        title_bar = f'White rectangular title bar at the VERY TOP of image with black text: "{segment_title}"'
        
        # Scene type specific simple visuals
        type_visuals = {
            "intro": f"Simple glowing orb or portal in center, welcoming feel",
            "explaining": f"Simple visual metaphor for {main_visual}, single focal point",
            "reaction": "Glowing exclamation mark or starburst effect",
            "transition": "Smooth flowing lines or gentle wave pattern",
            "pure_concept": "Simple geometric shapes floating in space",
            "data_visualization": "Simple chart or graph silhouette",
            "conclusion": "Simple checkmark or circular completion symbol"
        }
        
        visual_instruction = type_visuals.get(scene.scene_type, f"Simple illustration of {main_visual}")
        
        # Character instructions - character is @img1 when sent
        if scene.include_character:
            char_instruction = "Include stick figure character from @img1 in bottom-left corner, about 25% of frame height."
        else:
            char_instruction = "No characters in this scene."
        
        prompt = f"""
        BACKGROUND: Photorealistic deep space — deep navy to black with visible purple-pink nebula clouds, dense cold white star field, faint galaxy spiral suggestion.

        FOREGROUND (flat 2D cartoon layered over space background): {visual_instruction}. Bold black outlines on all cartoon shapes. Cel-shaded flat color fills — NO gradients inside cartoon shapes. Dominant palette: golden yellow, coral red, violet purple, warm orange, clean white. Warm glowing accent on key element. Scale contrast — cartoon element dramatically smaller than the vast cosmic background.

        {char_instruction}

        16:9 widescreen composite. No text, no labels, no 3D rendering, no anime style, no gradients inside cartoon shapes.
        """.strip()
        
        return " ".join(prompt.split())[:4000]


# =============================================================================
# PLACEHOLDER GENERATOR
# =============================================================================

class PlaceholderGenerator:
    """Generates placeholder images when AI generation fails."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def create_placeholder(self, scene: Scene, output_path: str) -> str:
        """Create a placeholder image for a failed scene."""
        img = Image.new('RGB', (self.config.image_width, self.config.image_height))
        draw = ImageDraw.Draw(img)
        
        # Gradient background
        for y in range(self.config.image_height):
            r = int(10 + (y / self.config.image_height) * 20)
            g = int(10 + (y / self.config.image_height) * 10)
            b = int(30 + (y / self.config.image_height) * 30)
            draw.line([(0, y), (self.config.image_width, y)], fill=(r, g, b))
        
        try:
            # Try Windows fonts first
            font_paths = [
                "C:\\Windows\\Fonts\\arial.ttf",
                "C:\\Windows\\Fonts\\segoeui.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
            font = None
            for fp in font_paths:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, 72)
                    break
            if not font:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        scene_text = f"Scene {scene.index + 1}"
        bbox = draw.textbbox((0, 0), scene_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (self.config.image_width - text_width) // 2
        y = self.config.image_height // 2 - 50
        
        for offset in [(2, 2), (-2, -2), (2, -2), (-2, 2)]:
            draw.text((x + offset[0], y + offset[1]), scene_text, fill=(0, 100, 150), font=font)
        draw.text((x, y), scene_text, fill=(0, 200, 255), font=font)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, 'PNG')
        
        return output_path


# =============================================================================
# VIDEO COMPILER
# =============================================================================

class VideoCompiler:
    """Compiles images and audio into final video using FFmpeg."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def find_intro_video(self, workspace: Path) -> Optional[str]:
        """Find intro video in the intro folder."""
        intro_dir = workspace / "intro"
        if not intro_dir.exists():
            return None
        
        # Look for video files
        for ext in ['*.mp4', '*.mov', '*.avi', '*.mkv']:
            videos = list(intro_dir.glob(ext))
            if videos:
                return str(videos[0])
        
        return None
    
    def compile(
        self, 
        scenes: List[Scene], 
        audio_path: str, 
        output_path: str,
        workspace: Optional[Path] = None
    ) -> bool:
        """Compile scenes into a video with audio and optional intro."""
        print(f"\n🎥 Compiling video...")
        
        # Find intro video
        intro_path = None
        if workspace:
            intro_path = self.find_intro_video(workspace)
            if intro_path:
                print(f"   ✓ Found intro video: {os.path.basename(intro_path)}")
        
        # Create concat file for images
        temp_dir = tempfile.mkdtemp()
        concat_path = os.path.join(temp_dir, "concat.txt")
        images_video_path = os.path.join(temp_dir, "images_video.mp4")
        
        try:
            # Write concat file with ABSOLUTE paths
            with open(concat_path, 'w', encoding='utf-8') as concat_file:
                for scene in scenes:
                    if scene.image_path and os.path.exists(scene.image_path):
                        # Use absolute path with forward slashes
                        abs_path = os.path.abspath(scene.image_path).replace('\\', '/')
                        concat_file.write(f"file '{abs_path}'\n")
                        concat_file.write(f"duration {scene.duration}\n")
                
                # Add last image again (FFmpeg quirk)
                if scenes and scenes[-1].image_path:
                    abs_path = os.path.abspath(scenes[-1].image_path).replace('\\', '/')
                    concat_file.write(f"file '{abs_path}'\n")
            
            # Ensure output directory exists
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            # Step 1: Create video from images with audio
            print(f"   Creating video from {len(scenes)} images...")
            
            cmd_images = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_path,
                '-i', audio_path,
                '-c:v', 'libx264',
                '-preset', 'medium',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-pix_fmt', 'yuv420p',
                '-r', str(self.config.fps),
                '-shortest',
            ]
            
            # If we have an intro, output to temp file; otherwise to final output
            if intro_path:
                cmd_images.append(images_video_path)
            else:
                cmd_images.append(output_path)
            
            result = subprocess.run(
                cmd_images, 
                capture_output=True, 
                text=True,
                timeout=600
            )
            
            if result.returncode != 0:
                print(f"   ⚠ FFmpeg error creating video: {result.stderr[-500:]}")
                return False
            
            # Step 2: If intro exists, concatenate intro + main video
            if intro_path and os.path.exists(images_video_path):
                print(f"   Adding intro video...")
                
                # Create concat file for intro + main
                final_concat_path = os.path.join(temp_dir, "final_concat.txt")
                with open(final_concat_path, 'w', encoding='utf-8') as f:
                    intro_abs = os.path.abspath(intro_path).replace('\\', '/')
                    main_abs = os.path.abspath(images_video_path).replace('\\', '/')
                    f.write(f"file '{intro_abs}'\n")
                    f.write(f"file '{main_abs}'\n")
                
                # Concatenate videos
                cmd_concat = [
                    'ffmpeg', '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', final_concat_path,
                    '-c', 'copy',
                    output_path
                ]
                
                result = subprocess.run(
                    cmd_concat,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode != 0:
                    # Try re-encoding if copy fails
                    print(f"   Re-encoding for compatibility...")
                    cmd_concat_reencode = [
                        'ffmpeg', '-y',
                        '-f', 'concat',
                        '-safe', '0',
                        '-i', final_concat_path,
                        '-c:v', 'libx264',
                        '-preset', 'medium',
                        '-crf', '23',
                        '-c:a', 'aac',
                        '-b:a', '192k',
                        output_path
                    ]
                    
                    result = subprocess.run(
                        cmd_concat_reencode,
                        capture_output=True,
                        text=True,
                        timeout=600
                    )
                    
                    if result.returncode != 0:
                        print(f"   ⚠ FFmpeg error adding intro: {result.stderr[-300:]}")
                        # Use the images-only video as fallback
                        import shutil
                        shutil.copy(images_video_path, output_path)
                        print(f"   ⚠ Using video without intro as fallback")
            
            # Verify output
            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                print(f"   ✓ Video compiled: {output_path} ({size_mb:.1f} MB)")
                return True
            
            return False
            
        except subprocess.TimeoutExpired:
            print("   ⚠ FFmpeg timed out")
            return False
        except FileNotFoundError:
            print("   ⚠ FFmpeg not found! Make sure it's installed and in PATH")
            return False
        except Exception as e:
            print(f"   ⚠ Compilation error: {e}")
            return False
        finally:
            # Cleanup temp files
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except:
                pass


# =============================================================================
# MAIN VIDEO AUTOMATION
# =============================================================================

class VideoAutomation:
    """Main orchestrator for video automation workflow."""
    
    def __init__(
        self, 
        api_key: str,
        workspace_dir: str = "video_workspace",
        config: Optional[Config] = None
    ):
        self.config = config or Config()
        self.workspace = Path(workspace_dir)
        self.api_key = api_key
        
        self.generator = AI33ImageGenerator(api_key, self.config)
        self.transcriber = AudioTranscriber(self.config)
        self.analyzer = SceneAnalyzer(self.config)
        self.placeholder_gen = PlaceholderGenerator(self.config)
        self.compiler = VideoCompiler(self.config)
        
        self._setup_workspace()
    
    def _setup_workspace(self):
        """Create workspace directories."""
        dirs = ['characters', 'style_references', 'audio', 'images', 'videos', 'scripts', 'intro']
        for d in dirs:
            (self.workspace / d).mkdir(parents=True, exist_ok=True)
    
    def _find_style_reference(self) -> Optional[str]:
        """Find a style reference image."""
        style_dir = self.workspace / "style_references"
        
        # Check for any image in style_references
        for ext in ['*.png', '*.jpg', '*.jpeg']:
            refs = list(style_dir.glob(ext))
            if refs:
                return str(refs[0])
        
        # Check images folder for any existing generated image
        images_dir = self.workspace / "images"
        if images_dir.exists():
            for subdir in images_dir.iterdir():
                if subdir.is_dir():
                    for ext in ['*.png', '*.jpg']:
                        refs = list(subdir.glob(ext))
                        if refs:
                            return str(refs[0])
        
        return None
    
    def _find_character(self) -> Optional[str]:
        """Find character image."""
        char_dir = self.workspace / "characters"
        
        candidates = [
            char_dir / "MC.png",
            char_dir / "mc.png",
            char_dir / "character.png",
        ]
        
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        
        # Check for any PNG in characters folder
        for png in char_dir.glob("*.png"):
            return str(png)
        
        return None
    
    def _save_scenes_progress(self, scenes: List[Scene], video_name: str):
        """Save current progress to file."""
        scenes_path = self.workspace / "scripts" / f"{video_name}_scenes.json"
        with open(scenes_path, 'w', encoding='utf-8') as f:
            json.dump([
                {
                    'index': s.index,
                    'text': s.text,
                    'start_time': s.start_time,
                    'end_time': s.end_time,
                    'duration': s.duration,
                    'scene_type': s.scene_type,
                    'include_character': s.include_character,
                    'image_path': s.image_path,
                    'status': s.generation_status
                }
                for s in scenes
            ], f, indent=2, ensure_ascii=False)
    
    def _check_existing_progress(self, video_name: str) -> Tuple[List[Scene], bool]:
        """Check if there's existing progress to resume."""
        scenes_path = self.workspace / "scripts" / f"{video_name}_scenes.json"
        
        if not scenes_path.exists():
            return [], False
        
        try:
            with open(scenes_path, 'r', encoding='utf-8') as f:
                scenes_data = json.load(f)
            
            # Count completed vs pending
            completed = sum(1 for s in scenes_data if s.get('status') == 'done')
            total = len(scenes_data)
            
            if completed > 0 and completed < total:
                print(f"\n⏸️  Found incomplete task: {completed}/{total} scenes completed")
                print(f"   Resume this task? (y/n): ", end="")
                
                try:
                    response = input().strip().lower()
                    if response in ['y', 'yes']:
                        # Reconstruct scenes
                        scenes = []
                        for s in scenes_data:
                            scene = Scene(
                                index=s['index'],
                                text=s['text'],
                                start_time=s['start_time'],
                                end_time=s['end_time'],
                                duration=s['duration'],
                                scene_type=s['scene_type'],
                                include_character=s['include_character'],
                                image_path=s.get('image_path'),
                                generation_status=s.get('status', 'pending')
                            )
                            scenes.append(scene)
                        return scenes, True
                except:
                    pass
            
            return [], False
            
        except Exception as e:
            print(f"   Could not load existing progress: {e}")
            return [], False
    
    def _generate_scene_image(
        self, 
        scene: Scene, 
        prompt_builder: PromptBuilder,
        character_path: Optional[str],
        style_ref_path: Optional[str],  # Not used anymore but kept for compatibility
        output_dir: Path
    ) -> GenerationResult:
        """Generate image for a single scene with retry logic."""
        output_path = str(output_dir / f"scene_{scene.index:04d}.png")
        prompt = prompt_builder.build_prompt(scene)
        scene.prompt = prompt
        
        print(f"\n   🎨 Scene {scene.index + 1} [{scene.scene_type}] {'+ Character' if scene.include_character else ''}")
        
        for attempt in range(self.config.max_retries):
            if attempt > 0:
                print(f"      🔄 Retry {attempt + 1}/{self.config.max_retries}")
            
            # Only pass character if scene includes it (saves credits!)
            result = self.generator.generate_image(
                prompt=prompt,
                output_path=output_path,
                character_path=character_path if scene.include_character else None,
                style_ref_path=None  # Don't send style ref anymore - described in prompt instead
            )
            
            result.scene_index = scene.index
            
            if result.success:
                scene.image_path = result.image_path
                scene.generation_status = "done"
                print(f"      ✓ Done in {result.generation_time:.1f}s")
                return result
            
            print(f"      ⚠ Failed: {result.error}")

            if attempt < self.config.max_retries - 1:
                delay = 60.0 if "429" in str(result.error) else self.config.retry_delay
                if delay > self.config.retry_delay:
                    print(f"      ⏳ Waiting {delay:.0f}s (rate limit)...")
                time.sleep(delay)
        
        # All retries failed
        print(f"      ❌ Creating placeholder")
        placeholder_path = self.placeholder_gen.create_placeholder(scene, output_path)
        scene.image_path = placeholder_path
        scene.generation_status = "failed"
        
        return GenerationResult(
            scene_index=scene.index,
            success=False,
            image_path=placeholder_path,
            error=result.error if result else "Unknown error"
        )
    
    def process(
        self,
        audio_path: str,
        video_name: str,
        character_rate: float = 0.35,
        workers: int = 5,
        video_title: str = "",
        script_json: Optional[str] = None
    ) -> Optional[str]:
        """
        Process audio into complete video.
        
        Args:
            audio_path: Path to input audio file
            video_name: Name for output files
            character_rate: Rate of character appearance (0.0-1.0)
            workers: Number of parallel workers
            video_title: Title to display in video
            script_json: Optional path to script JSON
            
        Returns:
            Path to output video or None if failed
        """
        print("\n" + "="*70)
        print("🎬 VIDEO AUTOMATION SYSTEM (AI33.pro Nano Banans)")
        print("="*70)
        
        # Validate inputs
        if not os.path.exists(audio_path):
            print(f"❌ Audio file not found: {audio_path}")
            return None
        
        # Find style reference (REQUIRED)
        style_ref_path = self._find_style_reference()
        if style_ref_path:
            print(f"✓ Style reference: {style_ref_path}")
        else:
            print("⚠ No style reference found in style_references/")
            print("  Images will be generated without style consistency")
        
        # Find character
        character_path = self._find_character()
        if character_path:
            print(f"✓ Character image: {character_path}")
        else:
            print("⚠ No character image found (will skip character in scenes)")
        
        # Get audio duration
        audio_duration = self.transcriber.get_audio_duration(audio_path)
        print(f"✓ Audio duration: {audio_duration:.1f} seconds")
        
        # Calculate costs
        num_scenes = max(1, int(audio_duration / self.config.seconds_per_image))
        estimated_cost = num_scenes * self.config.credits_per_image
        print(f"✓ Estimated scenes: {num_scenes}")
        print(f"✓ Estimated cost: {estimated_cost:,} credits")
        print(f"✓ Model: {self.config.ai33_model}")
        
        # Transcribe or load script
        if script_json and os.path.exists(script_json):
            print(f"\n📄 Loading script from: {script_json}")
            with open(script_json, 'r', encoding='utf-8') as f:
                script_data = json.load(f)
            segments = [
                TranscriptSegment(
                    text=s.get('text', ''),
                    start_time=s.get('start', 0),
                    end_time=s.get('end', 0)
                )
                for s in script_data.get('segments', [])
            ]
        else:
            segments = self.transcriber.transcribe(audio_path)
        
        # Save transcript
        transcript_path = self.workspace / "scripts" / f"{video_name}_transcript.json"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump([
                {'text': s.text, 'start': s.start_time, 'end': s.end_time}
                for s in segments
            ], f, indent=2, ensure_ascii=False)
        print(f"✓ Transcript saved: {transcript_path}")
        
        # Create scenes (or resume existing)
        existing_scenes, is_resuming = self._check_existing_progress(video_name)
        
        if is_resuming and existing_scenes:
            scenes = existing_scenes
            print(f"\n🔄 Resuming: {sum(1 for s in scenes if s.generation_status == 'done')}/{len(scenes)} already done")
        else:
            scenes = self.analyzer.create_scenes(
                segments=segments,
                audio_duration=audio_duration,
                character_rate=character_rate,
                video_title=video_title or video_name
            )
        
        # Setup output directories
        images_dir = self.workspace / "images" / video_name
        images_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize Claude prompt generator if API key provided
        claude_generator = None
        if self.config.use_claude_prompts and self.config.claude_api_key:
            claude_generator = ClaudePromptGenerator(
                api_key=self.config.claude_api_key,
                model=self.config.claude_model
            )
            print(f"✓ Claude API enabled for smart prompts")
        
        # Build prompt builder
        prompt_builder = PromptBuilder(self.config, video_title or video_name, claude_generator)
        prompt_builder.total_scenes = len(scenes)
        
        # Generate images
        pending_scenes = [s for s in scenes if s.generation_status != 'done']
        print(f"\n🎨 Generating {len(pending_scenes)} images ({len(scenes) - len(pending_scenes)} already done)...")
        print(f"   Using model: {self.config.ai33_model}")
        
        if not pending_scenes:
            print("   All images already generated!")
        
        total_credits = 0
        total_time = 0
        failed_count = 0
        
        # Sequential or parallel processing
        if workers == 1 or len(pending_scenes) <= 2:
            # Sequential processing
            for scene in pending_scenes:
                result = self._generate_scene_image(
                    scene, prompt_builder, character_path, style_ref_path, images_dir
                )
                total_credits += result.credits_used
                total_time += result.generation_time
                if not result.success:
                    failed_count += 1
                
                # Save progress after each scene
                self._save_scenes_progress(scenes, video_name)
        else:
            # Parallel processing
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        self._generate_scene_image,
                        scene, prompt_builder, character_path, style_ref_path, images_dir
                    ): scene
                    for scene in pending_scenes
                }
                
                for future in as_completed(futures):
                    scene = futures[future]
                    try:
                        result = future.result()
                        total_credits += result.credits_used
                        total_time += result.generation_time
                        if not result.success:
                            failed_count += 1
                    except Exception as e:
                        failed_count += 1
                        print(f"\n   ⚠ Scene {scene.index} error: {e}")
            
            # Save progress
            self._save_scenes_progress(scenes, video_name)
        
        # Summary
        print(f"\n📊 Generation Summary:")
        print(f"   AI33 credits used: {total_credits:,}")
        print(f"   Total generation time: {total_time:.1f}s")
        if len(pending_scenes) > 0:
            print(f"   Average per image: {total_time/len(pending_scenes):.1f}s")
        print(f"   Failed (placeholders): {failed_count}")
        
        # Claude API cost summary
        if claude_generator:
            cost_info = claude_generator.get_cost_estimate()
            print(f"\n📊 Claude API Usage:")
            print(f"   Input tokens: {cost_info['input_tokens']:,}")
            print(f"   Output tokens: {cost_info['output_tokens']:,}")
            print(f"   Estimated cost: ${cost_info['total_cost']:.4f}")
        
        # Final save of scenes data
        self._save_scenes_progress(scenes, video_name)
        
        # Compile video
        output_video = self.workspace / "videos" / f"{video_name}.mp4"
        
        success = self.compiler.compile(
            scenes=scenes,
            audio_path=audio_path,
            output_path=str(output_video),
            workspace=self.workspace  # Pass workspace for intro detection
        )
        
        if success:
            print(f"\n" + "="*70)
            print(f"✅ VIDEO COMPLETE!")
            print(f"="*70)
            print(f"   Output: {output_video}")
            print(f"   Resolution: {self.config.image_width}x{self.config.image_height}")
            print(f"   FPS: {self.config.fps}")
            print(f"   Scenes: {len(scenes)}")
            print(f"   Credits used: {total_credits:,}")
            return str(output_video)
        else:
            print("\n❌ Video compilation failed")
            return None


# =============================================================================
# COMMAND LINE INTERFACE
# =============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="YouTube Video Automation with AI33.pro (Nano Banans)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python video_automation_ai33.py --audio audio.mp3 --name my_video --ai33-key API_KEY
  
  # Full options
  python video_automation_ai33.py ^
      --audio "video_workspace\\audio\\test_audio.mp3" ^
      --name "my_video" ^
      --ai33-key "sk_xxx" ^
      --title "Amazing Science Facts" ^
      --character-rate 0.35 ^
      --workers 3
        """
    )
    
    parser.add_argument('--audio', '-a', required=True, help='Input audio file')
    parser.add_argument('--name', '-n', required=True, help='Output video name')
    parser.add_argument('--ai33-key', '-k', required=True, help='AI33.pro API key')
    parser.add_argument('--title', '-t', default='', help='Video title')
    parser.add_argument('--character-rate', '-c', type=float, default=0.35, help='Character rate (0-1)')
    parser.add_argument('--workers', '-w', type=int, default=5, help='Parallel workers')
    parser.add_argument('--workspace', '-d', default='video_workspace', help='Workspace directory')
    parser.add_argument('--script', '-s', default=None, help='Script JSON path')
    parser.add_argument('--seconds-per-image', type=float, default=6.0, help='Seconds per image')
    parser.add_argument('--model', '-m', default='bytedance-seedream-4.5', 
                        choices=['bytedance-seedream-4.5', 'bytedance-seedream-4', 
                                 'gpt-image-1.5', 'gpt-image-1', 'kling-omni-image', 
                                 'flux-2-pro', 'gemini-3-pro-image-preview'],
                        help='AI33 model (default: bytedance-seedream-4.5, cheapest option)')
    parser.add_argument('--claude-key', default='', help='Claude API key for smart prompts (optional)')
    parser.add_argument('--use-claude', action='store_true', help='Enable Claude API for intelligent prompts')
    
    args = parser.parse_args()
    
    # Create config
    config = Config()
    config.seconds_per_image = args.seconds_per_image
    config.ai33_model = args.model
    config.claude_api_key = args.claude_key
    config.use_claude_prompts = args.use_claude and bool(args.claude_key)
    config.default_character_rate = args.character_rate
    
    # Create automation instance
    automation = VideoAutomation(
        api_key=args.ai33_key,
        workspace_dir=args.workspace,
        config=config
    )
    
    # Process video
    result = automation.process(
        audio_path=args.audio,
        video_name=args.name,
        character_rate=args.character_rate,
        workers=args.workers,
        video_title=args.title,
        script_json=args.script
    )
    
    if result:
        print(f"\n🎉 Success! Video saved to: {result}")
        sys.exit(0)
    else:
        print("\n💔 Video generation failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
