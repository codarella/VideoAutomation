#!/usr/bin/env python3
"""
YouTube Video Automation V2 - Segment-Based Generation
=======================================================

This version:
- Splits video by "Number X" segments (Number 9, Number 8, etc.)
- Each segment has its own title (e.g., "The Hierarchy Problem")
- Variable image count based on content complexity
- Generates 10 images in parallel
- Adds title bar overlay after generation

Usage:
    python video_automation_v2.py --audio "audio.mp3" --name "my_video" --ai33-key "KEY"
"""

import os
import sys
import json
import time
import math
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

try:
    import requests
    from PIL import Image, ImageDraw, ImageFont
    from pydub import AudioSegment
except ImportError as e:
    print(f"Missing package: {e}")
    sys.exit(1)

try:
    from faster_whisper import WhisperModel
except ImportError:
    raise RuntimeError(
        "Faster-Whisper is not installed in this Python environment.\n"
        "Run: pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
        "Then restart the application."
    )



# =============================================================================
# AI33 MODEL SELECTOR
# =============================================================================

class AI33ModelSelector:
    """Fetches and displays available models with pricing."""
    
    def __init__(self, api_key: str, base_url: str = "https://api.ai33.pro"):
        self.api_key = api_key
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": api_key})
    
    def get_models(self) -> List[Dict]:
        """Fetch available models from API."""
        try:
            response = self.session.get(f"{self.base_url}/v1i/models", timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get('models', [])
            else:
                print(f"   ⚠ Failed to fetch models: {response.status_code}")
                return []
        except Exception as e:
            print(f"   ⚠ Error fetching models: {e}")
            return []
    
    def get_price(self, model_id: str, aspect_ratio: str = "16:9", 
                  resolution: str = "2K", assets: int = 1) -> int:
        """Get price for a specific model configuration."""
        try:
            response = self.session.post(
                f"{self.base_url}/v1i/task/price",
                json={
                    "model_id": model_id,
                    "generations_count": 1,
                    "model_parameters": {
                        "aspect_ratio": aspect_ratio,
                        "resolution": resolution
                    },
                    "assets": assets
                },
                timeout=30
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('credits', 0)
            return 0
        except:
            return 0
    
    def display_and_select(self) -> Optional[str]:
        """Display all models with pricing and let user select one."""
        print("\n" + "="*70)
        print("📋 FETCHING AI33 IMAGE MODELS & PRICING...")
        print("="*70)
        
        models = self.get_models()
        
        if not models:
            print("❌ Could not fetch models from API.")
            print("   Check your API key and internet connection.")
            return None
        
        # Get pricing for each model
        model_data = []
        print(f"\nFound {len(models)} models. Getting prices...\n")
        
        for i, model in enumerate(models):
            model_id = model.get('model_id', '')
            print(f"   [{i+1}/{len(models)}] {model_id}...", end=" ", flush=True)
            
            # Get prices
            price_0 = self.get_price(model_id, assets=0)
            price_1 = self.get_price(model_id, assets=1)
            
            model_data.append({
                'id': model_id,
                'max_gen': model.get('max_generations', 1),
                'aspect_ratios': model.get('aspect_ratios', []),
                'resolutions': model.get('resolutions', []),
                'supports_images': model.get('supports_images', False),
                'price_no_ref': price_0,
                'price_with_ref': price_1
            })
            print(f"{price_0} / {price_1} credits")
        
        # Sort by price (cheapest first)
        model_data.sort(key=lambda x: x['price_no_ref'] if x['price_no_ref'] > 0 else 999999)
        
        # Display table
        print("\n" + "="*70)
        print("MODEL PRICING (sorted by cost, cheapest first)")
        print("="*70)
        print(f"{'#':<4} {'Model ID':<40} {'Base':<10} {'+ Style Ref':<12}")
        print("-"*70)
        
        for i, m in enumerate(model_data, 1):
            price_no = f"{m['price_no_ref']:,}" if m['price_no_ref'] > 0 else "N/A"
            price_ref = f"{m['price_with_ref']:,}" if m['price_with_ref'] > 0 else "N/A"
            
            # Highlight known models
            name = m['id']
            if 'gemini-2.5-flash-image' in name.lower():
                name = f"🍌 {name} (Nano Banana)"
            elif 'gemini-3-pro-image' in name.lower():
                name = f"🍌 {name} (Nano Banana Pro)"
            elif 'gpt' in name.lower():
                name = f"🔥 {name}"
            elif 'flux' in name.lower():
                name = f"⚡ {name}"
            
            print(f"{i:<4} {name:<45} {price_no:<10} {price_ref:<12}")
        
        print("-"*70)
        print("Base = no reference images | + Style Ref = with 1 style reference")
        print("="*70)
        
        # Let user pick
        while True:
            try:
                choice = input(f"\n👉 Enter number (1-{len(model_data)}) or 'q' to use default: ").strip()
                
                if choice.lower() in ['q', '']:
                    print("Using default model from config.")
                    return None
                
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(model_data):
                        selected = model_data[idx]['id']
                        price = model_data[idx]['price_with_ref']
                        print(f"\n✅ Selected: {selected} ({price} credits/image with style ref)")
                        return selected
                    else:
                        print(f"   Please enter 1-{len(model_data)}")
                else:
                    # Check if it's a valid model ID typed directly
                    for m in model_data:
                        if m['id'].lower() == choice.lower():
                            print(f"\n✅ Selected: {m['id']}")
                            return m['id']
                    print("   Model not found. Enter a number or exact model ID.")
                    
            except KeyboardInterrupt:
                print("\n\nCancelled. Using default model from config.")
                return None
            except Exception as e:
                print(f"   Error: {e}")


# =============================================================================
# STYLE REFERENCE MANAGER
# =============================================================================

class StyleReferenceManager:
    """Manages style references with rotation to avoid repetition."""
    
    def __init__(self, style_paths: List[str], cooldown: int = 5):
        """
        Args:
            style_paths: List of paths to style reference images
            cooldown: Minimum images before a style can be reused
        """
        self.all_styles = style_paths.copy()
        self.cooldown = cooldown
        self.recent_used = []  # Track recently used styles
        self.lock = threading.Lock()
        
        if self.all_styles:
            random.shuffle(self.all_styles)
    
    def get_next(self) -> Optional[str]:
        """Get next style reference, avoiding recent ones."""
        if not self.all_styles:
            return None
        
        with self.lock:
            # Find styles not in cooldown
            available = [s for s in self.all_styles if s not in self.recent_used]
            
            # If all styles are in cooldown, use the oldest one
            if not available:
                available = self.all_styles.copy()
                # Remove oldest from recent to make room
                if self.recent_used:
                    self.recent_used.pop(0)
            
            # Pick random from available
            chosen = random.choice(available)
            
            # Add to recent used
            self.recent_used.append(chosen)
            
            # Keep recent list at cooldown size
            while len(self.recent_used) > self.cooldown:
                self.recent_used.pop(0)
            
            return chosen
    
    def get_count(self) -> int:
        """Get total number of style references."""
        return len(self.all_styles)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Configuration settings."""
    # API
    ai33_base_url: str = "https://api.ai33.pro"
    ai33_model: str = "bytedance-seedream-4.5"
    
    # Image settings
    aspect_ratio: str = "16:9"
    resolution: str = "2K"
    image_width: int = 1920
    image_height: int = 1080
    
    # Video
    fps: int = 30
    min_seconds_per_image: float = 3.0
    max_seconds_per_image: float = 8.0
    
    # Character
    character_rate: float = 0.20  # 20% of images have character
    
    # Style reference - always use for consistency
    use_style_reference: bool = True
    
    # Sync: delay each image N seconds into its scene window so it appears
    # when the narrator is saying the concept, not at the start of the sentence
    scene_display_offset: float = 2.5

    # Parallel generation
    max_workers: int = 10
    compile_workers: int = 3   # FFmpeg Ken Burns/clip pass — lower to avoid OOM on large videos
    
    # API settings
    poll_interval: float = 4.0
    max_poll_time: float = 300.0
    max_retries: int = 3

    # Compile-only mode: skip image generation, reuse existing images
    compile_only: bool = False

    # Prompts-only mode: stop after generating and saving prompts.json
    prompts_only: bool = False

    # Scene-text-only mode: export scene texts for Claude, skip LLM and image generation
    scene_text_only: bool = False

    # No-compile mode: generate images but skip the final video compile step
    no_compile: bool = False

    # Ken Burns slow-zoom/pan effect on each image
    ken_burns: bool = False

    # Crossfade dissolve between scenes (requires clip-based pipeline)
    crossfade: bool = False
    crossfade_duration: float = 0.4

    # Regen specific scene indices (1-based, e.g. [5, 12]) — force re-generate even if file exists
    regen_scenes: list = field(default_factory=list)

    # After generation, detect visually similar/duplicate images and auto-regenerate them
    find_dupes: bool = False
    dupe_threshold: int = 10  # Hamming distance threshold (0-64); lower = stricter


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Segment:
    """A numbered segment (e.g., 'Number 9: The Hierarchy Problem')"""
    number: int
    title: str
    text: str
    start_time: float
    end_time: float
    entries: List[Dict] = field(default_factory=list)
    images: List['SceneImage'] = field(default_factory=list)


@dataclass
class SceneImage:
    """A single image within a segment."""
    index: int
    segment_number: int
    segment_title: str
    text: str
    start_time: float
    end_time: float
    duration: float
    include_character: bool
    image_path: Optional[str] = None
    status: str = "pending"
    llm_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    is_title_card: bool = False
    audio_lock_time: float = 0.0  # exact audio timestamp this image is locked to


# =============================================================================
# SEGMENT PARSER
# =============================================================================

class SegmentParser:
    """Parses transcript into numbered segments."""
    
    def __init__(self, config: Config):
        self.config = config
    
    def time_to_seconds(self, time_str: str) -> float:
        """Convert time string (M:SS or MM:SS) to seconds."""
        parts = time_str.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0
    
    def load_json_transcript(self, json_path: str) -> Tuple[List[Dict], float]:
        """
        Load transcript from JSON file.
        Supports multiple formats including AI33 word-level.
        
        Returns: (transcript_entries, total_duration)
        """
        print(f"   Loading: {os.path.basename(json_path)}")
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        entries = []
        total_duration = 0
        
        # Debug: Show structure
        if isinstance(data, dict):
            print(f"   Keys found: {list(data.keys())[:5]}")
        elif isinstance(data, list) and data:
            print(f"   Array with {len(data)} items")
            if isinstance(data[0], dict):
                print(f"   First item keys: {list(data[0].keys())[:5]}")
        
        # ============================================================
        # FORMAT: AI33 word-level transcript
        # Array with objects containing: text, words[], language_code
        # ============================================================
        if isinstance(data, list) and data and isinstance(data[0], dict):
            first_item = data[0]
            
            # Check for AI33 word-level format
            if 'words' in first_item and 'text' in first_item:
                print(f"   Format: AI33 Word-Level Transcript")
                
                words = first_item.get('words', [])
                full_text = first_item.get('text', '')
                
                # Filter only actual words (not spacing)
                actual_words = [w for w in words if w.get('type') == 'word']
                
                if actual_words:
                    # Get total duration from last word
                    total_duration = actual_words[-1].get('end', 0)
                    
                    # Group words into ~4 second chunks for segments
                    chunk_duration = 4.0  # seconds per chunk
                    current_chunk_words = []
                    chunk_start = actual_words[0].get('start', 0)
                    
                    for word in actual_words:
                        current_chunk_words.append(word.get('text', ''))
                        word_end = word.get('end', 0)
                        
                        # Create entry when chunk is long enough or at end
                        if word_end - chunk_start >= chunk_duration or word == actual_words[-1]:
                            # Join words WITH SPACES
                            chunk_text = ' '.join(current_chunk_words)
                            entries.append({
                                'text': chunk_text.strip(),
                                'start': chunk_start,
                                'end': word_end
                            })
                            current_chunk_words = []
                            chunk_start = word_end
                    
                    print(f"   ✓ Created {len(entries)} segments from {len(actual_words)} words")
                    print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
                    
                    if entries:
                        preview = entries[0]['text'][:60] + "..." if len(entries[0]['text']) > 60 else entries[0]['text']
                        print(f"   Preview: \"{preview}\"")
                    
                    return entries, total_duration
        
        # ============================================================
        # FORMAT: Nexlev - {"transcript": [{"time": "0:00", "script": "..."}]}
        # ============================================================
        if isinstance(data, dict) and 'transcript' in data and isinstance(data['transcript'], list):
            transcript = data['transcript']
            if transcript and isinstance(transcript[0], dict) and 'time' in transcript[0] and 'script' in transcript[0]:
                print(f"   Format: Nexlev/YouTube ({len(transcript)} entries)")
                for i, entry in enumerate(transcript):
                    time_sec = self.time_to_seconds(entry['time'])
                    if i + 1 < len(transcript):
                        end_sec = self.time_to_seconds(transcript[i + 1]['time'])
                    else:
                        end_sec = time_sec + 3
                    entries.append({
                        'text': entry['script'],
                        'start': time_sec,
                        'end': end_sec
                    })
                total_duration = entries[-1]['end'] if entries else 0
                
                print(f"   ✓ Loaded {len(entries)} segments")
                print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
                return entries, total_duration
        
        # ============================================================
        # FORMAT: Segments - {"segments": [{"start": 0, "end": 5, "text": "..."}]}
        # ============================================================
        if isinstance(data, dict) and 'segments' in data:
            print(f"   Format: Segments array")
            for seg in data['segments']:
                entries.append({
                    'text': seg.get('text', '').strip(),
                    'start': float(seg.get('start', 0)),
                    'end': float(seg.get('end', 0))
                })
            total_duration = entries[-1]['end'] if entries else 0
            
            print(f"   ✓ Loaded {len(entries)} segments")
            print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
            return entries, total_duration
        
        # ============================================================
        # FORMAT: Transcription wrapper - {"transcription": {"segments": [...]}}
        # ============================================================
        if isinstance(data, dict) and 'transcription' in data:
            trans = data['transcription']
            if isinstance(trans, dict) and 'segments' in trans:
                print(f"   Format: Transcription wrapper")
                for seg in trans['segments']:
                    entries.append({
                        'text': seg.get('text', '').strip(),
                        'start': float(seg.get('start', 0)),
                        'end': float(seg.get('end', 0))
                    })
                total_duration = entries[-1]['end'] if entries else 0
                
                print(f"   ✓ Loaded {len(entries)} segments")
                print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
                return entries, total_duration
        
        # ============================================================
        # FORMAT: Result wrapper - {"result": {"segments": [...]}}
        # ============================================================
        if isinstance(data, dict) and 'result' in data:
            result = data['result']
            if isinstance(result, dict) and 'segments' in result:
                print(f"   Format: Result wrapper")
                for seg in result['segments']:
                    entries.append({
                        'text': seg.get('text', '').strip(),
                        'start': float(seg.get('start', 0)),
                        'end': float(seg.get('end', 0))
                    })
                total_duration = entries[-1]['end'] if entries else 0
                
                print(f"   ✓ Loaded {len(entries)} segments")
                print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
                return entries, total_duration
        
        # ============================================================
        # FALLBACK: Unknown format
        # ============================================================
        print(f"   ⚠ Unknown format - trying to extract text...")
        
        # Try to find text anywhere
        text = ""
        if isinstance(data, dict):
            text = data.get('text', '') or data.get('transcript', '') or data.get('content', '')
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            text = data[0].get('text', '')
        
        if text and len(text) > 50:
            # Estimate duration from text length (~15 chars per second)
            total_duration = max(60, len(text) / 15)
            entries.append({
                'text': text,
                'start': 0,
                'end': total_duration
            })
            print(f"   ✓ Extracted text ({len(text)} chars)")
            print(f"   ✓ Estimated duration: {total_duration:.1f}s")
        else:
            print(f"   ❌ Could not parse transcript!")
            total_duration = 60
            entries.append({'text': 'No transcript', 'start': 0, 'end': 60})
        
        return entries, total_duration
    
    # Word to number mapping
    WORD_TO_NUM = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
        'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
        'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20
    }
    
    def load_srt_transcript(self, srt_path: str) -> Tuple[List[Dict], float]:
        """
        Load transcript from SRT subtitle file.
        Returns: (transcript_entries, total_duration)
        """
        print(f"   Loading SRT: {os.path.basename(srt_path)}")
        
        entries = []
        
        with open(srt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse SRT format: index\ntimestamp --> timestamp\ntext\n\n
        blocks = re.split(r'\n\n+', content.strip())
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 2:
                # Find timestamp line
                timestamp_line = None
                text_start = 0
                for idx, line in enumerate(lines):
                    if '-->' in line:
                        timestamp_line = line
                        text_start = idx + 1
                        break
                
                if timestamp_line:
                    text_lines = lines[text_start:]
                    
                    # Parse timestamp: 00:00:00,099 --> 00:00:02,919
                    time_match = re.match(r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})', timestamp_line)
                    if time_match:
                        start_h, start_m, start_s, start_ms = map(int, time_match.groups()[:4])
                        end_h, end_m, end_s, end_ms = map(int, time_match.groups()[4:])
                        
                        start_time = start_h * 3600 + start_m * 60 + start_s + start_ms / 1000
                        end_time = end_h * 3600 + end_m * 60 + end_s + end_ms / 1000
                        
                        text = ' '.join(text_lines).strip()
                        
                        entries.append({
                            'text': text,
                            'start': start_time,
                            'end': end_time
                        })
        
        total_duration = entries[-1]['end'] if entries else 0
        
        print(f"   ✓ Loaded {len(entries)} subtitle blocks")
        print(f"   ✓ Duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
        
        if entries:
            preview = entries[0]['text'][:60] + "..." if len(entries[0]['text']) > 60 else entries[0]['text']
            print(f"   Preview: \"{preview}\"")
        
        return entries, total_duration
    
    def _word_to_number(self, word: str) -> int:
        """Convert written number word to integer."""
        return self.WORD_TO_NUM.get(word.lower(), 0)
    
    def parse_segments(self, entries: List[Dict], total_duration: float, manifest_path: str = None) -> Tuple[List[Segment], float]:
        """
        Parse transcript entries into segments.
        If manifest_path points to an existing segments_manifest.json, use it directly.
        Otherwise fall back to auto-detection via 'Number X' transcript scanning.
        """
        if manifest_path and os.path.exists(manifest_path):
            print(f"   📋 Using segments manifest: {os.path.basename(manifest_path)}")
            return self._parse_segments_from_manifest(manifest_path, entries, total_duration)

        return self._auto_parse_segments(entries, total_duration)
    
    def _create_segment(self, seg_data: Dict) -> Segment:
        """Create a Segment object from parsed data."""
        full_text = ' '.join(e['text'] for e in seg_data['entries'])
        return Segment(
            number=seg_data['number'],
            title=seg_data['title'],
            text=full_text,
            start_time=seg_data['start_time'],
            end_time=seg_data['end_time'],
            entries=seg_data['entries']
        )

    def _parse_segments_from_manifest(self, manifest_path: str, entries: List[Dict], total_duration: float) -> Tuple[List['Segment'], float]:
        """
        Build Segment objects from a user-defined segments_manifest.json.
        Synthesises the two 'Number X' word entries that create_images_for_segment
        expects, using the manifest's card_duration to set their timestamps.
        """
        with open(manifest_path, encoding='utf-8') as f:
            manifest = json.load(f)

        seg_defs = sorted(manifest.get('segments', []), key=lambda s: s['start_time'])
        if not seg_defs:
            print("   ⚠ Manifest has no segments — falling back to auto-detection")
            return self._auto_parse_segments(entries, total_duration)

        intro_end_time = seg_defs[0]['start_time']
        segments = []

        for idx, seg_def in enumerate(seg_defs):
            start      = float(seg_def['start_time'])
            card_dur   = float(seg_def.get('card_duration', 2.5))
            end        = float(seg_defs[idx + 1]['start_time']) if idx + 1 < len(seg_defs) else total_duration
            num_val    = int(seg_def['number'])
            title      = seg_def.get('title', f'Segment {num_val}')

            # Synthesise the two "Number X" word entries so that
            # create_images_for_segment can detect and render the number card.
            half = card_dur / 2.0
            number_entry = {'text': 'Number', 'start': start,          'end': start + half}
            digit_entry  = {'text': str(num_val), 'start': start + half, 'end': start + card_dur}

            # Real transcript words that fall within this segment (after the card)
            content_entries = [e for e in entries if start + card_dur <= e['start'] < end]

            all_entries = [number_entry, digit_entry] + content_entries
            full_text   = ' '.join(e['text'] for e in all_entries)

            seg = Segment(
                number=num_val,
                title=title,
                text=full_text,
                start_time=start,
                end_time=end,
                entries=all_entries,
            )
            segments.append(seg)
            print(f"   ✓ Manifest seg #{num_val}: '{title}' @ {start:.2f}s  (card {card_dur}s)")

        return segments, intro_end_time

    def _auto_parse_segments(self, entries: List[Dict], total_duration: float) -> Tuple[List['Segment'], float]:
        """Original 'Number X' auto-detection logic, extracted for reuse."""
        segments = []
        current_segment = None
        intro_end_time = total_duration

        def clean_word(w):
            return re.sub(r'[^a-zA-Z0-9]', '', str(w).lower())

        i = 0
        while i < len(entries):
            word = clean_word(entries[i]['text'])
            if word == "number" and i + 1 < len(entries):
                next_word = clean_word(entries[i + 1]['text'])
                num_val = None
                if next_word in self.WORD_TO_NUM:
                    num_val = self.WORD_TO_NUM[next_word]
                elif next_word.isdigit():
                    num_val = int(next_word)
                if num_val is not None:
                    boundary_time = entries[i]['start']
                    if current_segment:
                        current_segment['end_time'] = boundary_time
                        segments.append(self._create_segment(current_segment))
                    else:
                        intro_end_time = boundary_time
                        print(f"   📺 Intro ends EXACTLY at {intro_end_time:.2f}s (start of 'Number')")
                    title_words = []
                    j = i + 2
                    while j < min(i + 15, len(entries)):
                        w_text = entries[j]['text']
                        title_words.append(w_text)
                        if re.search(r'[.!?]', w_text):
                            break
                        j += 1
                    title = " ".join(title_words).strip()
                    title = re.sub(r'\s+', ' ', title).title()
                    if len(title) > 40:
                        title = title[:40] + "..."
                    print(f"   ✓ Seg #{num_val}: '{title}' | starts @ {boundary_time:.2f}s")
                    current_segment = {
                        'number': num_val, 'title': title,
                        'start_time': boundary_time, 'end_time': total_duration,
                        'entries': [entries[i], entries[i + 1]],
                    }
                    i += 2
                    continue
            if current_segment:
                current_segment['entries'].append(entries[i])
            i += 1

        if current_segment:
            current_segment['end_time'] = total_duration
            segments.append(self._create_segment(current_segment))

        if intro_end_time < 1.0 and segments:
            print("   🚨 WARNING: Intro duration is < 1 second.")

        return segments, intro_end_time

    def create_images_for_segment(self, segment: Segment, start_index: int) -> List[SceneImage]:
        """
        Create image scenes based strictly on transcript timing.
        Rule 5: Scene.start = transcript timestamp, Scene.end = next_scene.start
        """
        images = []
        blocks = []

        # -----------------------------------------------------------------------
        # CHANGE 1: Number Card Prefix — split first two entries into their own
        # block so the plain white number card only lasts while "Number X" is spoken.
        # -----------------------------------------------------------------------
        entries_to_process = segment.entries
        if len(segment.entries) >= 2:
            first_word  = re.sub(r'[^a-zA-Z0-9]', '', segment.entries[0]['text']).lower()
            second_word = re.sub(r'[^a-zA-Z0-9]', '', segment.entries[1]['text']).lower()
            is_numeric  = second_word.isdigit() or second_word in self.WORD_TO_NUM

            if first_word == 'number' and is_numeric:
                nc_start = segment.entries[0]['start']
                nc_end   = segment.entries[1]['end']
                blocks.append({
                    'text':       segment.entries[0]['text'] + ' ' + segment.entries[1]['text'],
                    'start_time': nc_start,
                    'end_time':   nc_end
                })
                entries_to_process = segment.entries[2:]

        current_block = []
        current_text  = ""
        block_start   = entries_to_process[0]['start'] if entries_to_process else segment.end_time

        # CHANGE 2: Fast cuts for first 60 seconds (initial selection)
        if block_start < 60.0:
            target_duration = random.uniform(2.0, 3.0)
        else:
            target_duration = random.choice([
                random.uniform(2.0, 3.5),  # Quick cut
                random.uniform(4.0, 5.5),  # Standard
                random.uniform(4.0, 5.5),  # Standard (weighted more)
            ])

        for entry in entries_to_process:
            current_block.append(entry)
            current_text += " " + entry['text']

            block_duration = entry['end'] - block_start
            ends_with_punctuation = bool(re.search(r'[.!?]$', entry['text'].strip()))

            # Trigger block boundary if we hit the target duration (and preferably land on punctuation if close)
            # OR if we drastically exceed the target duration.
            if (block_duration >= target_duration and ends_with_punctuation) or block_duration >= (target_duration + 2.0):
                blocks.append({
                    'text': current_text.strip(),
                    'start_time': block_start,
                    'end_time': entry['end']
                })
                current_block = []
                current_text = ""
                block_start = entry['end']

                # CHANGE 2: Fast cuts for first 60 seconds (re-selection after block boundary)
                if block_start < 60.0:
                    target_duration = random.uniform(2.0, 3.0)
                else:
                    target_duration = random.choice([
                        random.uniform(2.0, 3.5),  # Quick cut
                        random.uniform(4.0, 5.5),  # Standard
                        random.uniform(4.0, 5.5),  # Standard (weighted more)
                    ])

        # Handle trailing text
        if current_block:
            end_time = current_block[-1]['end']
            if blocks and (end_time - block_start) < 2.0:
                blocks[-1]['end_time'] = end_time
                blocks[-1]['text'] += " " + current_text.strip()
            else:
                blocks.append({
                    'text': current_text.strip(),
                    'start_time': block_start,
                    'end_time': end_time
                })

        if not blocks:
            # Fallback if no valid blocks formed
            blocks.append({
                'text': segment.text,
                'start_time': segment.start_time,
                'end_time': segment.end_time
            })

        # CHANGE 3: Split list-pattern blocks into per-item scenes
        blocks = self._split_list_blocks(blocks, segment.entries, segment.end_time)

        # Ensure gapless sequence up to segment.end_time
        blocks[-1]['end_time'] = segment.end_time
            
        print(f"      Segment '{segment.title[:25]}' ({segment.end_time - segment.start_time:.2f}s) → {len(blocks)} images")
        
        for i, block in enumerate(blocks):
            image_text = block['text'][:200]
            start_time = block['start_time']
            end_time = block['end_time']
            image_duration = end_time - start_time
            
            if image_duration <= 0.0:
                raise ValueError(f"Invalid image duration {image_duration:.2f}s for scene at {start_time:.2f}s")
            
            # Debug logging Rule 7
            print(f"         Scene {i+1}: [{start_time:.2f}s - {end_time:.2f}s] (Duration: {image_duration:.2f}s)")
            if image_duration > 15.0:
                print(f"         🚨 WARNING: Scene duration ({image_duration:.2f}s) exceeds 15s top-10 format norm!")
            
            include_char = random.random() < self.config.character_rate
            
            images.append(SceneImage(
                index=start_index + i,
                segment_number=segment.number,
                segment_title=segment.title,
                text=image_text,
                start_time=start_time,
                end_time=end_time,
                duration=image_duration,
                include_character=include_char
            ))
            
        return images

    # -----------------------------------------------------------------------
    # CHANGE 3 helpers: List detection and per-item block splitting
    # -----------------------------------------------------------------------

    def _detect_list_items(self, text: str):
        """
        Detect whether text contains a list pattern.
        Returns list of item strings, or None if no list detected.
        """
        if len(text.strip()) < 15:
            return None

        # Strategy A: comma-separated items (2-8 words each, 3+ items)
        comma_parts = [p.strip() for p in text.split(',')]
        if len(comma_parts) >= 3:
            word_counts = [len(p.split()) for p in comma_parts if p]
            if word_counts and all(2 <= wc <= 8 for wc in word_counts):
                return [p for p in comma_parts if p]

        # Strategy B: sentence-split items (./?/!) with 3+ short sentences (<10 words each)
        sentence_parts = re.split(r'(?<=[.?!])\s+', text.strip())
        sentence_parts = [p.strip() for p in sentence_parts if p.strip()]
        if len(sentence_parts) >= 3:
            word_counts = [len(p.split()) for p in sentence_parts]
            if all(wc < 10 for wc in word_counts):
                return sentence_parts

        return None

    def _find_item_start_time(self, item_text: str, entries: list, search_from: float):
        """
        Find the start timestamp of the first meaningful word of item_text
        within entries, starting search from search_from seconds.
        Returns entry['start'] if found, else None.
        """
        words = item_text.strip().split()
        if not words:
            return None
        first_word = re.sub(r'[^a-zA-Z0-9]', '', words[0]).lower()
        if not first_word and len(words) > 1:
            first_word = re.sub(r'[^a-zA-Z0-9]', '', words[1]).lower()
        if not first_word:
            return None

        for entry in entries:
            if entry['start'] < search_from:
                continue
            entry_word = re.sub(r'[^a-zA-Z0-9]', '', entry['text']).lower()
            if entry_word == first_word:
                return entry['start']
        return None

    def _split_list_blocks(self, blocks: list, entries: list, segment_end_time: float) -> list:
        """
        Post-process blocks: detect list patterns and split into per-item blocks.
        Number card blocks are excluded. Falls back to original block if any
        item timestamp is missing or timing is degenerate.
        """
        result = []
        for block in blocks:
            block_text = block['text'].strip()

            # Skip number cards
            if re.match(r'^Number\s+\w+', block_text, re.IGNORECASE) and len(block_text.split()) <= 3:
                result.append(block)
                continue

            items = self._detect_list_items(block_text)
            if items is None:
                result.append(block)
                continue

            # Try to find timestamps for all items
            item_starts = []
            search_from = block['start_time']
            all_found = True

            for item in items:
                ts = self._find_item_start_time(item, entries, search_from)
                if ts is None:
                    all_found = False
                    break
                item_starts.append(ts)
                search_from = ts + 0.01

            if not all_found:
                result.append(block)
                continue

            # Build per-item blocks
            item_blocks = []
            degenerate = False
            for idx, (item, ts) in enumerate(zip(items, item_starts)):
                end_ts = item_starts[idx + 1] if idx + 1 < len(item_starts) else segment_end_time
                if end_ts <= ts:
                    degenerate = True
                    break
                item_blocks.append({'text': item, 'start_time': ts, 'end_time': end_ts})

            if degenerate:
                result.append(block)
            else:
                result.extend(item_blocks)

        return result


# =============================================================================
# AI33 IMAGE GENERATOR
# =============================================================================

class AI33Generator:
    """Generates images using AI33 API."""
    
    def __init__(self, api_key: str, config: Config):
        self.api_key = api_key
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"xi-api-key": api_key})
        self.lock = threading.Lock()
        self.total_credits = 0
    
    def generate(self, prompt: str, output_path: str,
                 character_path: Optional[str] = None,
                 style_ref_path: Optional[str] = None,
                 always_include_mc: Optional[str] = None,
                 negative_prompt: Optional[str] = None) -> Tuple[bool, str, int]:
        """
        Generate an image.
        
        Asset order:
        - @img1 = style reference (for background/style)
        - @img2 = MC character (always sent so AI knows the character to use/replace with)
        
        Returns: (success, error_message, credits_used)
        """
        # Build prompt with image references
        # @img1 = style reference
        # @img2 = MC character (sent for reference even if not in scene, so AI knows what MC looks like)
        
        refs = []
        if style_ref_path:
            refs.append("@img1")
        if character_path or always_include_mc:
            refs.append(f"@img{len(refs) + 1}")
        
        if refs:
            final_prompt = f"{' '.join(refs)} {prompt}"
        else:
            final_prompt = prompt
        
        data = {
            'prompt': final_prompt,
            'model_id': self.config.ai33_model,
            'generations_count': '1',
            'model_parameters': json.dumps({
                'aspect_ratio': self.config.aspect_ratio,
                'resolution': self.config.resolution
            })
        }
        if negative_prompt:
            data['negative_prompt'] = negative_prompt
        
        files = []
        file_handles = []
        
        try:
            # Style reference FIRST (becomes @img1)
            if style_ref_path and os.path.exists(style_ref_path):
                fh = open(style_ref_path, 'rb')
                file_handles.append(fh)
                files.append(('assets', (os.path.basename(style_ref_path), fh, 'image/png')))
            
            # MC Character SECOND (becomes @img2) - use character_path or always_include_mc
            mc_path = character_path or always_include_mc
            if mc_path and os.path.exists(mc_path):
                fh = open(mc_path, 'rb')
                file_handles.append(fh)
                files.append(('assets', (os.path.basename(mc_path), fh, 'image/png')))
            
            # Submit request
            response = self.session.post(
                f"{self.config.ai33_base_url}/v1i/task/generate-image",
                data=data,
                files=files if files else None,
                timeout=120
            )
            
            if response.status_code != 200:
                try:
                    err = response.json()
                    msg = err.get('message', response.text[:200])
                except:
                    msg = response.text[:200]
                return False, f"API {response.status_code}: {msg}", 0
            
            result = response.json()
            
            if not result.get('success') and not result.get('task_id'):
                return False, f"No task_id: {result.get('message', result)}", 0
            
            task_id = result.get('task_id')
            credits = result.get('estimated_credits', 500)
            remaining = result.get('ec_remain_credits', '?')
            
            print(f"      ⏳ Task: {task_id[:10]}... ({credits} cr, {remaining} left)")
            
        except requests.exceptions.ConnectionError as e:
            return False, f"Connection error: {e}", 0
        except Exception as e:
            return False, f"Request error: {e}", 0
        finally:
            for fh in file_handles:
                try:
                    fh.close()
                except:
                    pass
        
        # Poll for completion
        start_time = time.time()
        while (time.time() - start_time) < self.config.max_poll_time:
            time.sleep(self.config.poll_interval)
            
            try:
                status_resp = self.session.get(
                    f"{self.config.ai33_base_url}/v1/task/{task_id}",
                    timeout=30
                )
                
                if status_resp.status_code != 200:
                    continue
                
                status_data = status_resp.json()
                status = status_data.get('status', '')
                
                if status == 'done':
                    images = status_data.get('metadata', {}).get('result_images', [])
                    if images:
                        image_url = images[0].get('imageUrl')
                        if image_url:
                            # Download
                            img_resp = requests.get(image_url, timeout=60)
                            if img_resp.status_code == 200:
                                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                                with open(output_path, 'wb') as f:
                                    f.write(img_resp.content)

                                # Validate the saved file is a readable image
                                try:
                                    with Image.open(output_path) as _img:
                                        _img.load()
                                except Exception:
                                    os.remove(output_path)
                                    return False, "Downloaded image is corrupt (invalid PNG data)", 0

                                with self.lock:
                                    self.total_credits += credits

                                return True, "", credits
                    
                    return False, "No image in result", 0
                
                elif status == 'error':
                    err_msg = status_data.get('error_message', 'Unknown error')
                    print(f"      🔍 Full error response: {status_data}")
                    return False, f"Generation error: {err_msg}", 0
                    
            except Exception as e:
                continue
        
        return False, f"Timeout after {self.config.max_poll_time}s", 0


# =============================================================================
# LOCAL LLM PROMPT GENERATOR
# =============================================================================

class LocalLLMPromptGenerator:
    """
    Generates image prompts AND scene plans using a local LLM.
    The LLM analyzes the script and makes ALL creative decisions.
    
    Loads guidelines from llm_guidelines.json for detailed rules.
    """
    
    def __init__(self, provider: str = "ollama", model: str = "qwen2.5:7b", 
                 base_url: str = None, guidelines_path: str = None):
        self.provider = provider.lower()
        self.model = model
        
        if base_url:
            self.base_url = base_url
        elif self.provider == "ollama":
            self.base_url = "http://localhost:11434"
        elif self.provider == "lmstudio":
            self.base_url = "http://localhost:1234"
        else:
            self.base_url = None  # claude provider uses CLI, no URL needed
        
        # Load guidelines
        self.guidelines = self._load_guidelines(guidelines_path)
        
        self.enabled = False
        self._test_connection()
    
    def _load_guidelines(self, path: str = None) -> dict:
        """Load guidelines from JSON file."""
        search_paths = [
            path,
            "llm_guidelines.json",
            "video_workspace/llm_guidelines.json",
            os.path.join(os.path.dirname(__file__), "llm_guidelines.json")
        ]
        
        for p in search_paths:
            if p and os.path.exists(p):
                try:
                    with open(p, 'r', encoding='utf-8') as f:
                        guidelines = json.load(f)
                        print(f"   ✓ Loaded LLM guidelines from {p}")
                        return guidelines
                except Exception as e:
                    print(f"   ⚠ Error loading guidelines from {p}: {e}")
        
        print("   ⚠ No guidelines file found - using defaults")
        return {}
    
    def _get_system_prompt(self) -> str:
        """Build system prompt from learned style framework."""
        return """You are a visual prompt writer for an educational science YouTube channel. You write image generation prompts in the 2D Western Cartoon animation style — bold, irreverent, chaotic sci-fi comedy aesthetic.

══ CORE STYLE: 2D WESTERN CARTOON ══
Every image must be in 2D Western Cartoon animation style:
- Bold, slightly wobbly/irregular black outlines — hand-drawn feel, NOT clean digital precision
- Flat cel-shading: flat color fills with ONE level of shadow maximum. NO gradients inside shapes.
- Occasional highlight blob on key elements
- Backgrounds are BUSY and MAXIMALIST: alien machinery, sci-fi gadgets, beakers, swirling portal vortexes, alien planet terrain, neon nebulae — irreverent and full of chaotic energy
- Palette: toxic/acid greens (#39FF14, #7CFC00), electric blues (#00B4FF), deep alien purples (#9B59B6), sickly yellows (#F0E130), neon pinks, muted gray/flesh tones — bright neons against dark or mid-tone backgrounds
- Characters: STICK FIGURES ONLY. Round head, dot eyes, minimal line body and limbs. No detailed anatomy. Stick figures express emotions through: sweat drops, bulging eyes, wavy distress lines, arms flailing, mouths agape.
- NO photorealistic elements. NO anime style. NO realistic human faces. NO clean gradients.

══ FILMMAKER PHILOSOPHY ══
Before writing, identify:
1. WHERE IS THE VIEWER positioned? (INSIDE / AS / BETWEEN / WITNESS / SCALE)
2. What does it FEEL LIKE to be there?

The universe in this channel feels chaotic, alive, and irreverent — like a mad scientist would explain it while something explodes in the background.

══ LITERAL TRANSLATION RULE ══
Translate the spoken scene text almost directly into a visual.
If the script says "a probe drifted for 40 years" — show a stick figure probe drifting, looking bewildered.
If the script says "scientists discovered a signal" — show stick figure scientists at a dish, surrounded by sci-fi chaos.
Do NOT replace literal content with a metaphor.

CONTEXT RULE: The literal content tells you WHAT to show. The broader scene context tells you the emotional tone — panicked, awed, frantic, triumphant.

══ SCENE TYPE RULES ══
ESTABLISH (first introduction — "is a", "known as", "called", "what is"):
→ Wide framing. Subject centered. Chaotic alien environment establishing the scale and weirdness.

DETAIL (property explanation — "because", "consists of", "made of", "behaves"):
→ Tight framing on one specific aspect. Stick figure examining/pointing at the detail.

CLIMAX (discovery or proof — "discovered", "proved", "found", "confirmed", "first time"):
→ Maximum visual drama. Deep neon colors. Subject fills 60-70% of frame. Stick figure reacting with full-body shock.

REACTION (consequence — "means that", "therefore", "which means", "consequence"):
→ Show aftermath. Stick figure stunned or processing. Background still busy and chaotic.

CHANGE (before→after — "instead", "but", "however", "actually", "turned out"):
→ Split frame vertically. Left = wrong/expected state (muted). Right = correct state (full neon palette).

══ ZOOM LEVELS ══
COSMIC (universe/galaxy/cosmos/space): subject 5-10% of frame. Vast alien space dominates.
HUMAN (lab/scientist/experiment): subject human-sized, 30-50% of frame. Lab environment packed with gadgets.
CLOSE (detail/surface/structure): subject 50-70% of frame.
EXTREME CLOSE (interior/inside/deep/micro): subject fills 80%+.

══ CHARACTER RULES ══
Step 1: Named person (scientist, historical figure)? → ONE stick figure. Round head, dot eyes, line body. Optional lab coat drawn as simple rectangle. Expression must match emotional context.
Step 2: Human performing a scientific act (observing, discovering, measuring)? → ONE stick figure at the instrument.
Step 3: Pure concept, phenomenon, or cosmic event? → NO characters. Let the environment and event be the subject.

Stick figure expressions:
- Shocked/horrified: bulging circle eyes, sweat drops, mouth wide open (O shape), arms raised or hands on head
- Triumphant: both stick arms raised, simple smile line
- Confused: wavy question mark nearby, head tilted, arms spread
- Concentrated: leaning forward, simple furrowed brow lines
- Awestruck: mouth open (O), eyes wide circles, one arm pointing

══ MOTION DIRECTION ══
All moving elements must specify direction explicitly.
Default: left-to-right (forward motion, discovery, progression).
Reversed: decay, failure, regression → right-to-left.

══ SUBJECT POSITION ══
CENTER: confrontation, certainty, declaration
OFF-CENTER LEFT: approaching, anticipation
OFF-CENTER RIGHT: aftermath, consequence
BOTTOM: stable, foundational
TOP: vast, overwhelming

══ ABSOLUTE NO-TEXT RULE ══
ZERO text, letters, numbers, labels, captions, or speech bubbles anywhere in the image.
ONLY exception: if the scene is specifically about a math/physics equation — render ONLY that equation as glowing abstract symbols in neon colors.

══ PROMPT FORMAT ══
"2D Western Cartoon animation. [Environment: vivid alien sci-fi backdrop — colors, specific elements, chaotic details]. [Viewer position stated explicitly]. [Central subject — what it is, what it's doing, stick figure expression/body language if character present]. [Secondary elements if any]. [Zoom level applied]. [Motion direction if applicable]. Bold wobbly black outlines, flat cel-shading, no gradients, no text anywhere."

Output ONLY the image prompt. No explanations. No scene type labels. No preamble. No commentary."""
    
    @staticmethod
    def _find_claude_exe():
        """Find the claude CLI executable, checking PATH and common fallback locations."""
        import shutil, os
        # Standard PATH lookup (finds claude.exe or claude.cmd)
        found = shutil.which("claude") or shutil.which("claude.cmd")
        if found:
            return found
        # Common fallback locations on Windows
        fallbacks = [
            os.path.expanduser("~/.local/bin/claude.exe"),
            os.path.expanduser("~/.local/bin/claude"),
        ]
        for path in fallbacks:
            if os.path.isfile(path):
                return path
        return None

    def _test_connection(self):
        """Test if local LLM is available, auto-starting Ollama if needed."""
        try:
            if self.provider == "ollama":
                try:
                    resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
                    if resp.status_code == 200:
                        self.enabled = True
                        print(f"   ✓ Ollama connected ({self.model})")
                        return
                except:
                    pass
                # Ollama not running — try to start it
                print(f"   Ollama not running, attempting to start...")
                try:
                    subprocess.Popen(
                        ["ollama", "serve"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    )
                    # Wait up to 15s for it to be ready
                    for i in range(15):
                        time.sleep(1)
                        try:
                            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
                            if resp.status_code == 200:
                                self.enabled = True
                                print(f"   ✓ Ollama started and connected ({self.model})")
                                return
                        except:
                            pass
                    print(f"   ⚠ Ollama did not start in time")
                except Exception as e:
                    print(f"   ⚠ Could not start Ollama: {e}")
            elif self.provider == "lmstudio":
                resp = requests.get(f"{self.base_url}/v1/models", timeout=5)
                if resp.status_code == 200:
                    self.enabled = True
                    print(f"   ✓ LM Studio connected")
            elif self.provider == "claude":
                exe = self._find_claude_exe()
                if exe:
                    self.claude_exe = exe
                    self.enabled = True
                    print(f"   ✓ Claude CLI available ({exe})")
                else:
                    print(f"   ⚠ Claude CLI not found in PATH")
        except:
            pass
        if not self.enabled:
            print(f"   ⚠ LLM not available ({self.provider})")
    
    _MATH_KEYWORDS = [
        "equation", "formula", "e=mc", "=mc²", "equals", "calculate", "calcul",
        "integral", "derivative", "math", "σ", "π", "θ", "ω", "Δ", "∑", "∫", "√",
        "λ", "²", "³", "≈", "mass-energy", "energy equals", "frequency", "wavelength",
    ]

    @staticmethod
    def _is_math_scene(text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        return any(kw.lower() in t for kw in LocalLLMPromptGenerator._MATH_KEYWORDS)

    def generate_prompt(self, scene_text: str, segment_title: str,
                        include_character: bool, segment_text: str = "") -> str:
        """Generate an image prompt using local LLM."""
        if not self.enabled:
            return None

        context = f"SEGMENT CONTEXT:\n{segment_text[:600]}\n\n" if segment_text else ""

        try:
            is_math = self._is_math_scene(scene_text) or self._is_math_scene(segment_title)

            # Decide character instruction using the learned decision tree
            if include_character:
                char_instruction = (
                    "CHARACTER: Include ONE stick figure — simple round head, dot eyes, minimal line body and limbs, no detailed anatomy. "
                    "Exaggerated cartoon emotion matching the scene (sweat drops for panic, bulging circle eyes for shock, "
                    "both arms raised for triumph, arms spread/tilted head for confusion). Optional simple rectangle as lab coat if appropriate."
                )
            else:
                char_instruction = "CHARACTER: None. This is a pure physics concept scene. Do NOT add any human figures."

            text_rule = (
                "TEXT: This scene is about a math/physics equation — render ONLY that equation as glowing abstract symbols in space. No other text."
                if is_math else
                "TEXT: Absolutely zero text, letters, numbers, labels, or captions anywhere."
            )

            user_message = f"""Write a single image generation prompt for this scene.

TOPIC: {segment_title}
{context}SCENE (words being spoken right now): {scene_text[:300]}
{char_instruction}
{text_rule}

Silently determine the following before writing (do NOT output these — they are thinking steps only):
- Scene type: ESTABLISH / DETAIL / CLIMAX / REACTION / CHANGE
- Zoom level: COSMIC / HUMAN / CLOSE / EXTREME CLOSE
- Viewer position: INSIDE / AS / BETWEEN / WITNESS / SCALE
- Motion direction: left-to-right / right-to-left / none
- Subject position in frame: center / off-center left / off-center right / bottom / top
- Literal read: what objects, events, and actions are literally described in the scene text?
- Emotional tone: what does this moment feel like? (wonder, dread, realization, curiosity, urgency?)
- Environment mood: what should the illustrated space backdrop look and feel like to match that tone?

Then write the prompt — showing the literal scene content as an action inside the illustrated environment.
Output ONLY the final image prompt starting with "2D Western Cartoon animation." — no labels, no reasoning, just the prompt:"""

            if self.provider == "ollama":
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": f"{self._get_system_prompt()}\n\n{user_message}",
                        "stream": False,
                        "options": {
                            "temperature": 0.75,
                            "num_predict": 450
                        }
                    },
                    timeout=300
                )
                if response.status_code == 200:
                    result = response.json().get('response', '').strip()
                    if result:
                        return self._clean_prompt(result)
                    else:
                        print(f"      ⚠ Ollama returned empty response (status 200)")
                else:
                    print(f"      ⚠ Ollama returned status {response.status_code}: {response.text[:200]}")

            elif self.provider == "lmstudio":
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._get_system_prompt()},
                            {"role": "user", "content": user_message}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 450
                    },
                    timeout=120
                )
                if response.status_code == 200:
                    choices = response.json().get('choices', [])
                    if choices:
                        result = choices[0].get('message', {}).get('content', '').strip()
                        if result:
                            return self._clean_prompt(result)

            elif self.provider == "claude":
                full_prompt = f"{self._get_system_prompt()}\n\n{user_message}"
                exe = getattr(self, 'claude_exe', None) or self._find_claude_exe() or "claude"
                result = subprocess.run(
                    [exe, "-p", full_prompt],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60
                )
                if result.returncode == 0 and result.stdout.strip():
                    return self._clean_prompt(result.stdout.strip())
                else:
                    print(f"      ⚠ Claude CLI error: {result.stderr[:200]}")

        except Exception as e:
            print(f"      ⚠ LLM error: {e}")

        return None
    
    def plan_scenes(self, full_transcript: str, entries: List[Dict], total_duration: float) -> List[Dict]:
        """
        Use LLM to analyze the full transcript and plan CONTENT scenes only.
        
        Number title cards and intro are handled by code - LLM only plans content.
        
        Returns a list of scene dictionaries.
        """
        if not self.enabled:
            print("   ⚠ LLM not available for scene planning")
            return None
        
        print("   🤖 LLM analyzing script for content scenes...")
        
        # Build transcript with timestamps
        transcript_with_times = ""
        for entry in entries[:50]:  # Limit to first 50 entries to avoid token limits
            transcript_with_times += f"[{entry['start']:.1f}s]: {entry['text']}\n"
        
        user_message = f"""Analyze this transcript and plan CONTENT images only.

IMPORTANT:
- SKIP "intro" scenes (the intro video is used)
- SKIP "title_card" scenes for "Number X" (code generates these)
- ONLY plan "content" scenes that illustrate the explanation

PACING RULES (CRITICAL):
You MUST vary the durations! Do not make all scenes 5 seconds long.
- Quick scenes (2-3s) for lists or action
- Standard (4-5s) for normal talking
- Long (6-8s) for complex points
- Dramatic (8-12s) for conclusions
Your scenes MUST connect seamlessly (Scene A ends exactly when Scene B starts).

TRANSCRIPT:
{transcript_with_times}

TOTAL DURATION: {total_duration:.1f}s

For each content scene, specify:
- start_time: When this scene starts (in seconds)
- end_time: When this scene ends
- spoken_text: What's being said (for reference)
- prompt: Detailed image prompt (NO TEXT IN IMAGE)
- include_character: true/false (use sparingly, ~20%)

Output JSON:
{{"scenes": [
  {{"start_time": X, "end_time": Y, "type": "content", "spoken_text": "...", "prompt": "2D cartoon illustration...", "include_character": false}}
]}}

ONLY output valid JSON:"""

        try:
            if self.provider == "lmstudio":
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._get_system_prompt()},
                            {"role": "user", "content": user_message}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 3000
                    },
                    timeout=300
                )
                if response.status_code == 200:
                    choices = response.json().get('choices', [])
                    if choices:
                        result = choices[0].get('message', {}).get('content', '').strip()
                        if result:
                            return self._parse_scene_plan(result)
            
            elif self.provider == "ollama":
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": f"{self._get_system_prompt()}\n\n{user_message}",
                        "stream": False,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 3000
                        }
                    },
                    timeout=300
                )
                if response.status_code == 200:
                    result = response.json().get('response', '').strip()
                    if result:
                        return self._parse_scene_plan(result)
        
        except Exception as e:
            print(f"   ⚠ LLM scene planning error: {e}")
        
        return None
    
    def _parse_scene_plan(self, llm_output: str) -> List[Dict]:
        """Parse the LLM's scene plan JSON output."""
        try:
            # Clean up the output
            llm_output = llm_output.strip()
            
            # Remove markdown code blocks if present
            if "```json" in llm_output:
                llm_output = llm_output.split("```json")[1].split("```")[0]
            elif "```" in llm_output:
                llm_output = llm_output.split("```")[1].split("```")[0]
            
            llm_output = llm_output.strip()
            
            # Parse JSON
            data = json.loads(llm_output)
            
            # It could be a list directly, or under a 'scenes' key
            scenes = data if isinstance(data, list) else data.get('scenes', [])
            
            # Filter out any title_card or intro scenes (code handles these)
            content_scenes = [s for s in scenes if s.get('type', 'content') == 'content']
            
            # Normalize keys to match what process() expects
            for s in content_scenes:
                if 'start' in s and 'start_time' not in s:
                    s['start_time'] = s['start']
                if 'end' in s and 'end_time' not in s:
                    s['end_time'] = s['end']
                if 'image_prompt' in s and 'prompt' not in s:
                    s['prompt'] = s['image_prompt']
            
            if content_scenes:
                print(f"   ✓ LLM planned {len(content_scenes)} content scenes")
                for i, scene in enumerate(content_scenes[:3]):
                    print(f"      {i+1}. @ {scene.get('start_time', 0):.1f}s - {scene.get('prompt', '')[:40]}...")
                if len(content_scenes) > 3:
                    print(f"      ... and {len(content_scenes) - 3} more")
            
            return content_scenes
            
        except json.JSONDecodeError as e:
            print(f"   ⚠ Failed to parse LLM scene plan: {e}")
            return None
    
    def _clean_prompt(self, prompt: str) -> str:
        """Clean up the LLM output."""
        # Remove common prefixes
        prefixes_to_remove = [
            "**IMAGE PROMPT:**",
            "**Image Prompt:**",
            "**PROMPT:**",
            "IMAGE PROMPT:",
            "Here's the prompt:",
            "Here is the prompt:",
            "Here's your prompt:",
            "Prompt:",
            "```",
        ]
        for prefix in prefixes_to_remove:
            if prompt.strip().startswith(prefix):
                prompt = prompt.strip()[len(prefix):].strip()

        # Remove trailing backticks and markdown
        prompt = prompt.replace("```", "").strip()

        # If the prompt ends mid-sentence (no punctuation), trim to last complete sentence
        if prompt and prompt[-1] not in ".!?\"'":
            last = max(prompt.rfind('. '), prompt.rfind('! '), prompt.rfind('? '))
            if last > len(prompt) // 2:  # only trim if we keep most of the prompt
                prompt = prompt[:last + 1].strip()

        return prompt


# =============================================================================
# CLAUDE API PROMPT GENERATOR (batch, context-aware)
# =============================================================================

class ClaudeAPIPromptGenerator:
    """
    Uses the Anthropic API to generate ALL image prompts in a single call.
    Claude sees the full video structure, enabling consistent visual style,
    narrative progression, and thematic coherence across all scenes.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.enabled = False
        self.model    = model
        try:
            import anthropic as _anthropic
            self._anthropic = _anthropic
            self.client     = _anthropic.Anthropic(api_key=api_key)
            self.enabled    = True
            print(f"   ✓ Claude API ready ({model})")
        except ImportError:
            print("   ⚠ anthropic package not installed — run: pip install anthropic")

    @staticmethod
    def _get_system_prompt() -> str:
        return LocalLLMPromptGenerator._get_system_prompt(None)

    def generate_all_prompts(self, content_scenes: list, seg_text_map: dict) -> dict:
        """
        Send every scene to Claude in one streaming API call.
        Returns {img.index: prompt_str} for each scene that got a prompt.
        """
        if not self.enabled or not content_scenes:
            return {}

        # Build a readable scene list grouped by segment (includes title cards)
        scene_lines = []
        current_seg = None
        for i, img in enumerate(content_scenes):
            if img.segment_number != current_seg:
                current_seg = img.segment_number
                scene_lines.append(
                    f"\n-- SEGMENT {img.segment_number}: \"{img.segment_title}\" --"
                )
                # Inject full segment narration so Claude understands the complete story arc
                full_text = seg_text_map.get(img.segment_number, "")
                if full_text:
                    scene_lines.append(f"FULL NARRATION: \"{full_text}\"")
                scene_lines.append("SCENES:")
            if img.is_title_card:
                dur = img.end_time - img.start_time
                scene_lines.append(
                    f"Scene {i + 1} | {img.start_time:.2f}s-{img.end_time:.2f}s "
                    f"({dur:.1f}s) [NUMBER CARD: {img.segment_number}]"
                )
            else:
                char_note = " [include scientist figure]" if img.include_character else ""
                dur = img.end_time - img.start_time
                scene_lines.append(
                    f"Scene {i + 1} | {img.start_time:.2f}s-{img.end_time:.2f}s "
                    f"({dur:.1f}s){char_note} | {img.text[:250]}"
                )

        n            = len(content_scenes)
        scenes_text  = "\n".join(scene_lines)
        user_message = (
            f"Here is the complete scene list for this video ({n} scenes total).\n"
            f"Each segment begins with its FULL NARRATION — read it entirely before writing "
            f"prompts for that segment. Use it to understand the story arc, key concepts, "
            f"and emotional beats, then write prompts that build a coherent visual narrative "
            f"across the segment's scenes.\n"
            f"\n{scenes_text}\n\n"
            f"For scenes marked [NUMBER CARD: N]: write exactly this prompt, replacing N with the actual digit: "
            f"\"Pure white background filling the entire 16:9 frame. "
            f"Single large bold black number N centered. "
            f"No other elements, no decorative details, no gradients, completely clean. "
            f"16:9 widescreen. No text or labels anywhere in the image.\"\n"
            f"\n"
            f"For all other scenes, apply this thinking before writing the prompt:\n"
            f"1. Re-read the FULL NARRATION of this segment — what is the story arc?\n"
            f"2. Which scene type fits this scene's role in that arc? (ESTABLISH/DETAIL/CLIMAX/REACTION/CHANGE)\n"
            f"3. LITERAL READ: What objects, events, and actions are literally described in this scene text?\n"
            f"4. CONTEXT & TONE: What is the broader emotional context? (wonder, dread, realization, urgency?)\n"
            f"5. ENVIRONMENT: What does the illustrated space backdrop look and feel like given that tone?\n"
            f"6. FREEZE-FRAME: Show the literal content as an action inside that environment.\n"
            f"7. What was the previous scene — vary the composition and zoom level.\n"
            f"8. Is this the first scene of a segment? Use a simple establishing/wide shot.\n"
            f"9. Is this the last scene of a segment? Use peak-drama or consequence framing.\n"
            f"10. Does a character appear in this segment? Use identical design to earlier scenes.\n"
            f"\n"
            f"Write ONE image generation prompt per scene. Follow ALL style rules exactly.\n"
            f"Scenes marked [include scientist figure] must include ONE rounded expressive figure.\n"
            f"Return a JSON array with exactly {n} objects:\n"
            f'[{{"scene": 1, "prompt": "..."}}, {{"scene": 2, "prompt": "..."}}, '
            f'..., {{"scene": {n}, "prompt": "..."}}]\n'
            f"Output ONLY the JSON array. No preamble, no commentary, no code fences."
        )

        try:
            print(f"   Sending {n} scenes to Claude API ({self.model})...")
            with self.client.messages.stream(
                model      = self.model,
                max_tokens = 65536,
                system     = ClaudeAPIPromptGenerator._get_system_prompt(),
                messages   = [{"role": "user", "content": user_message}],
            ) as stream:
                final = stream.get_final_message()

            text = next(
                (b.text for b in final.content if b.type == "text"), ""
            ).strip()

            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```", 1)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.rsplit("```", 1)[0].strip()

            data   = json.loads(text)
            result = {}
            for item in data:
                scene_num = item.get("scene")
                prompt    = (item.get("prompt") or "").strip()
                if isinstance(scene_num, int) and 1 <= scene_num <= n and prompt:
                    result[content_scenes[scene_num - 1].index] = prompt

            # Token usage + cost estimate
            usage = final.usage
            in_tok  = usage.input_tokens
            out_tok = usage.output_tokens
            # Pricing per 1M tokens (update if model changes)
            PRICES = {
                "claude-opus-4-6":   (5.00, 25.00),
                "claude-sonnet-4-6": (3.00, 15.00),
                "claude-haiku-4-5":  (1.00,  5.00),
            }
            in_p, out_p = PRICES.get(self.model, (5.00, 25.00))
            cost = (in_tok / 1_000_000 * in_p) + (out_tok / 1_000_000 * out_p)
            print(f"   ✓ Claude returned {len(result)}/{n} prompts")
            print(f"   📊 Tokens: {in_tok:,} in / {out_tok:,} out  |  Est. cost: ${cost:.4f} USD")
            return result

        except Exception as e:
            print(f"   ⚠ Claude API batch generation error: {e}")
            import traceback; traceback.print_exc()
            return {}


# =============================================================================
# NUMBER TITLE CARD GENERATOR
# =============================================================================

class NumberTitleCardGenerator:
    """
    Generates NUMBER TITLE CARDS using code, NOT AI.
    
    Format: 
    - White background
    - Large BLACK number in center
    - Topic name is added as title bar SEPARATELY
    
    This is NOT sent to AI image generator.
    """
    
    def __init__(self, config: Config):
        self.config = config
        self.width = config.image_width
        self.height = config.image_height
    
    def generate(self, number: int, output_path: str) -> bool:
        """
        Generate a number title card.
        
        Args:
            number: The countdown number (10, 9, 8, etc.)
            output_path: Where to save the image
        
        Returns:
            True if successful
        """
        try:
            # Create white background
            img = Image.new('RGB', (self.width, self.height), color=(255, 255, 255))
            draw = ImageDraw.Draw(img)
            
            # Load font - try to get a bold font
            number_text = str(number)
            font_size = int(self.height * 0.5)  # Large number, 50% of height
            
            font = None
            for fp in [
                "C:\\Windows\\Fonts\\arialbd.ttf",
                "C:\\Windows\\Fonts\\Arial Bold.ttf", 
                "C:\\Windows\\Fonts\\impact.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
            ]:
                if os.path.exists(fp):
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except:
                        pass
            
            if not font:
                # Fallback - try to load default with larger size
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
                except:
                    font = ImageFont.load_default()
            
            # Calculate text position (centered)
            bbox = draw.textbbox((0, 0), number_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            x = (self.width - text_width) // 2
            y = (self.height - text_height) // 2
            
            # Draw BLACK number
            draw.text((x, y), number_text, fill=(0, 0, 0), font=font)
            
            # Save
            img.save(output_path, 'PNG')
            return True
            
        except Exception as e:
            print(f"   ⚠ Error generating title card: {e}")
            return False


# =============================================================================
# TITLE BAR OVERLAY
# =============================================================================

class TitleBarOverlay:
    """Adds title bar to images."""
    
    def __init__(self, config: Config):
        self.config = config
        self.bar_height = int(config.image_height * 0.08)
    
    def add_title(self, image_path: str, title: str) -> bool:
        """Add white title bar with text to top of image."""
        try:
            img = Image.open(image_path).convert('RGB')
            
            if img.size != (self.config.image_width, self.config.image_height):
                img = img.resize((self.config.image_width, self.config.image_height), Image.Resampling.LANCZOS)
            
            draw = ImageDraw.Draw(img)
            
            # White bar
            draw.rectangle([0, 0, self.config.image_width, self.bar_height], fill=(255, 255, 255))
            draw.line([(0, self.bar_height), (self.config.image_width, self.bar_height)], fill=(0, 0, 0), width=2)
            
            # Font
            font_size = int(self.bar_height * 0.6)
            font = None
            for fp in ["C:\\Windows\\Fonts\\arialbd.ttf", "C:\\Windows\\Fonts\\arial.ttf",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
                if os.path.exists(fp):
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except:
                        pass
            
            if not font:
                font = ImageFont.load_default()
            
            # Center text
            bbox = draw.textbbox((0, 0), title, font=font)
            x = (self.config.image_width - (bbox[2] - bbox[0])) // 2
            y = (self.bar_height - (bbox[3] - bbox[1])) // 2 - 5
            
            draw.text((x, y), title, fill=(0, 0, 0), font=font)
            img.save(image_path, 'PNG')
            return True
            
        except Exception as e:
            print(f"      ⚠ Title error: {e}")
            return False


# =============================================================================
# PROMPT BUILDER
# =============================================================================

class PromptBuilder:
    """Builds image prompts - can use local LLM or built-in templates."""
    
    def __init__(self, local_llm: LocalLLMPromptGenerator = None):
        self.local_llm = local_llm
    
    def build(self, scene: SceneImage) -> str:
        """Build prompt for a scene - uses LLM prompt if available, otherwise generates."""
        
        # If scene has an LLM-generated prompt, use it directly
        if hasattr(scene, 'llm_prompt') and scene.llm_prompt:
            print(f"      ✓ Using LLM prompt ({len(scene.llm_prompt)} chars)")
            return scene.llm_prompt

        # Try local LLM if available for individual prompt generation
        if self.local_llm and self.local_llm.enabled:
            print(f"      🤖 LLM generating prompt for scene {scene.index + 1}...")
            llm_prompt = self.local_llm.generate_prompt(
                scene.text,
                scene.segment_title,
                scene.include_character
            )
            if llm_prompt:
                print(f"      ✓ LLM prompt ready ({len(llm_prompt)} chars)")
                return llm_prompt
            else:
                print(f"      ⚠ LLM failed, using template")
        
        # Fall back to built-in template
        return self._build_template(scene)
    
    def _build_template(self, scene: SceneImage) -> str:
        """Built-in template prompt."""
        
        # Extract key concepts from the scene text
        words = re.findall(r'\b[a-zA-Z]{4,}\b', scene.text.lower())
        stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'will', 'would', 'could', 'should',
                     'about', 'what', 'when', 'where', 'which', 'their', 'there', 'just', 'into', 'your',
                     'they', 'them', 'than', 'then', 'some', 'very', 'much', 'such', 'only', 'also'}
        keywords = [w for w in words if w not in stopwords][:4]
        
        # Main concept for the image
        if keywords:
            main_concept = ', '.join(keywords[:3])
        else:
            main_concept = "cosmic phenomenon"

        # Character instruction
        if scene.include_character:
            char_instruction = """
INCLUDE THE STICK FIGURE CHARACTER (2D Western Cartoon style):
- Simple round head with bold wobbly black outline
- Dot eyes — can be normal dots or wide bulging circles for shock/awe
- Minimal line body and limbs — stick figure, no detailed anatomy
- Exaggerated cartoon emotion matching the scene: sweat drops for panic, bulging eyes for shock, arms raised for triumph, arms spread for confusion
- Position: left side of frame, about 25-30% of frame height
- Character is REACTING to or OBSERVING the main visual
- Add sweat drops, wavy distress lines, or nearby "?" if appropriate"""
        else:
            char_instruction = """
NO CHARACTERS in this image - pure background/concept visualization only."""

        prompt = f"""Create a 2D cartoon illustration for educational science YouTube video.

SCENE CONCEPT: {main_concept}
SEGMENT: {scene.segment_title}

ART STYLE (CRITICAL — 2D Western Cartoon style):
- Bold, slightly wobbly/irregular black outlines — hand-drawn feel, NOT clean digital precision
- Flat cel-shading: flat color fills with ONE level of shadow, NO gradients inside shapes
- Vivid alien sci-fi background: neon nebulae in acid greens/purples/pinks, alien terrain, portal vortexes, or lab packed with sci-fi gadgets and beakers — BUSY and maximalist
- Palette: acid green (#39FF14), electric blue (#00B4FF), alien purple (#9B59B6), sickly yellow (#F0E130), neon pink, muted gray
- Characters are stick figures ONLY

VISUAL APPROACH - choose ONE:
A) ALIEN CONCEPT SCENE: Vivid alien/sci-fi environment visualizing the concept, maximalist background full of gadgets
B) COSMIC SCENE: Alien space visualization with vivid neon nebulae, weird alien terrain, portal effects
C) STICK FIGURE REACTION SCENE: Stick figure reacting to the phenomenon with exaggerated cartoon emotion

{char_instruction}

COMPOSITION:
- Main subject centered or slightly off-center
- Clean, uncluttered, high contrast
- 16:9 aspect ratio

DO NOT INCLUDE (HARD RULES — VIOLATION = REJECTED):
- ZERO text, words, numbers, labels, signs, captions anywhere in image — ONLY exception: a specific physics/math equation when the scene is explicitly about that equation
- ZERO realistic human figures, faces, or bodies (ONLY stick figures allowed)
- NO 3D rendered look, NO CGI
- NO photorealistic style
- NO gradients inside cartoon shapes
- NO clean precise digital art lines
- NO anime style

OUTPUT: 2D Western Cartoon flat cel-shaded illustration. Bold wobbly outlines. Vivid neon palette. Stick figures only."""
        
        return " ".join(prompt.split())


# =============================================================================
# AUDIO TRANSCRIBER
# =============================================================================

class FasterWhisperTranscriber:
    """Strictly transcribes audio using Faster-Whisper to extract word-level timestamps."""
    
    def __init__(self, model_size="small"):
        print("   Loading Faster-Whisper model...")
        self.model = WhisperModel(
            model_size,
            device="cuda",
            compute_type="float16"  # Changed from "int8_float16" to fix CUBLAS_STATUS_NOT_SUPPORTED
        )
        print("Faster-Whisper initialized with CUDA support.")
        print("Faster-Whisper loaded successfully. Word-level timestamps enabled.")
    
    def transcribe(self, audio_path: str) -> Tuple[str, List[Dict], float]:
        """
        Transcribe audio file.
        Returns: (full_text, entries, duration)
        """
        print(f"Transcribing audio: {audio_path}")
        
        # Get audio duration
        try:
            audio = AudioSegment.from_file(audio_path)
            duration = len(audio) / 1000.0
            print(f"   Duration: {duration:.1f}s ({duration/60:.1f} min)")
        except Exception as e:
            print(f"   ⚠ Could not determine duration: {e}")
            duration = 0.0
            
        print("   Transcribing with Faster-Whisper (word_timestamps=True)...")
        
        # Let errors bubble up to halt execution
        segments, info = self.model.transcribe(
            audio_path,
            word_timestamps=True,
            beam_size=1,        # greedy decoding — uses ~5x less decoder memory than default beam_size=5
        )
        
        entries = []
        full_text_parts = []
        
        for segment in segments:
            for word in segment.words:
                entries.append({
                    'text': word.word.strip(),
                    'start': word.start,
                    'end': word.end
                })
                # Add spaces back for full_text since we strip() above
                full_text_parts.append(word.word)
        
        full_text = "".join(full_text_parts)
        
        print(f"   ✓ {len(entries)} words transcribed")
        
        return full_text, entries, duration


# =============================================================================
# VIDEO COMPILER
# =============================================================================

class VideoCompiler:
    """Compiles images into video with intro support."""
    
    def __init__(self, config: Config):
        self.config = config
    
    # Ken Burns zoom patterns: (zoom expr, x expr, y expr) — cycles across scenes
    _KB_PATTERNS = [
        ("min(zoom+0.0006,1.5)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),       # zoom in center
        ("if(lte(zoom,1.0),1.5,max(1.001,zoom-0.0006))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),  # zoom out
        ("1.12", "0", "ih/2-(ih/zoom/2)"),                                         # pan right
        ("1.12", "iw-(iw/zoom)", "ih/2-(ih/zoom/2)"),                              # pan left
        ("min(zoom+0.0006,1.3)", "0", "0"),                                         # zoom in top-left
        ("min(zoom+0.0006,1.3)", "iw-(iw/zoom)", "ih-(ih/zoom)"),                  # zoom in bottom-right
    ]

    def _generate_placeholder_image(self, scene_num: int, out_path: str):
        """Generate a dark placeholder PNG for scenes that failed image generation."""
        w, h = self.config.image_width, self.config.image_height
        img = Image.new('RGB', (w, h), color=(20, 20, 30))
        draw = ImageDraw.Draw(img)
        try:
            font_large = ImageFont.truetype("arialbd.ttf", 120)
            font_small = ImageFont.truetype("arial.ttf", 60)
        except IOError:
            try:
                font_large = ImageFont.truetype("LiberationSans-Bold.ttf", 120)
                font_small = ImageFont.truetype("LiberationSans.ttf", 60)
            except IOError:
                font_large = font_small = ImageFont.load_default()
        lines = [
            (f"Scene {scene_num}", font_large, (220, 60, 60)),
            ("Image Generation Failed", font_small, (200, 200, 200)),
            ("Use  Regen Missing  to fix", font_small, (140, 140, 140)),
        ]
        total_h = sum(draw.textbbox((0, 0), t, font=f)[3] + 30 for t, f, _ in lines)
        y = (h - total_h) / 2
        for text, font, color in lines:
            bbox = draw.textbbox((0, 0), text, font=font)
            x = (w - (bbox[2] - bbox[0])) / 2
            draw.text((x, y), text, fill=color, font=font)
            y += bbox[3] - bbox[1] + 30
        img.save(out_path)

    def _compile_clips(self, images: List[SceneImage], temp_dir: str) -> Optional[str]:
        """
        Ken Burns + optional crossfade pipeline.
        Generates one MP4 clip per image (with zoompan if ken_burns=True),
        then concatenates with optional xfade dissolves.
        Returns path to final images.mp4 or None on failure.
        """
        fps = self.config.fps
        fade_dur = self.config.crossfade_duration

        def make_clip(img: SceneImage, pattern_idx: int) -> Optional[str]:
            clip_path = os.path.join(temp_dir, f"kb_{img.index:04d}.mp4")
            frames = max(int(math.ceil(img.duration * fps)) + 1, 1)  # +1 ensures zoompan never runs short
            if self.config.ken_burns:
                z, x, y = self._KB_PATTERNS[pattern_idx % len(self._KB_PATTERNS)]
                vf = (f"scale=3840:2160:force_original_aspect_ratio=increase,"
                      f"crop=3840:2160,"
                      f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps={fps},"
                      f"setsar=1")
            else:
                vf = ("scale=1920:1080:force_original_aspect_ratio=increase,"
                      "crop=1920:1080,setsar=1")
            cmd = [
                'ffmpeg', '-y', '-loop', '1', '-framerate', str(fps),
                '-i', img.image_path,
                '-t', f"{img.duration:.6f}",
                '-vf', vf,
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-pix_fmt', 'yuv420p', '-r', str(fps),
                clip_path
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode == 0 and os.path.exists(clip_path):
                return clip_path
            print(f"      ⚠ Clip failed scene {img.index + 1}: {r.stderr[-120:]}")
            return None

        # ── Per-image audio sync map ─────────────────────────────────────────────
        print(f"\n   📍 Per-image audio sync map:")
        sync_map_path = os.path.join(temp_dir, "sync_map.txt")
        with open(sync_map_path, 'w') as _sm:
            _sm.write("idx\tlock_time\tend_time\tduration\timage_path\n")
            for _i, _img in enumerate(images):
                _lock = getattr(_img, 'audio_lock_time', _img.start_time) or _img.start_time
                if _img.image_path and os.path.exists(_img.image_path):
                    print(f"      [{_i+1:3d}] {_lock:8.3f}s → {_img.end_time:.3f}s  ({_img.duration:.3f}s)  scene_{_img.index:04d}")
                    _sm.write(f"{_i+1}\t{_lock:.6f}\t{_img.end_time:.6f}\t{_img.duration:.6f}\t{_img.image_path}\n")
                else:
                    print(f"      [{_i+1:3d}] {_lock:8.3f}s → {_img.end_time:.3f}s  MISSING — absorbed by neighbour")
                    _sm.write(f"{_i+1}\t{_lock:.6f}\t{_img.end_time:.6f}\t{_img.duration:.6f}\tMISSING\n")

        effect = "Ken Burns" if self.config.ken_burns else "Clip"
        print(f"\n   🎞️  {effect} pass ({len(images)} images, {self.config.compile_workers} parallel)...")
        valid = [(i, img) for i, img in enumerate(images)
                 if img.image_path and os.path.exists(img.image_path)]

        clips_by_idx: dict = {}
        done = 0
        with ThreadPoolExecutor(max_workers=self.config.compile_workers) as ex:
            future_map = {ex.submit(make_clip, img, i): (i, img) for i, img in valid}
            for f in as_completed(future_map):
                i, img = future_map[f]
                clip = f.result()
                if clip:
                    clips_by_idx[i] = (img.duration, clip)
                done += 1
                if done % 5 == 0 or done == len(valid):
                    print(f"      {done}/{len(valid)} clips rendered")

        ordered = [(clips_by_idx[i][0], clips_by_idx[i][1])
                   for i in sorted(clips_by_idx)]

        if not ordered:
            print("   ❌ No clips generated")
            return None

        output = os.path.join(temp_dir, "images.mp4")

        if self.config.crossfade and len(ordered) > 1:
            print(f"   ✨ Applying {fade_dur}s crossfade between {len(ordered)} clips...")
            inputs = []
            for _, clip_path in ordered:
                inputs += ['-i', clip_path]

            filter_parts = []
            offset = 0.0
            prev_label = "[0:v]"
            for i in range(1, len(ordered)):
                offset += ordered[i - 1][0] - fade_dur
                out_label = "[outv]" if i == len(ordered) - 1 else f"[v{i}]"
                filter_parts.append(
                    f"{prev_label}[{i}:v]xfade=transition=fade"
                    f":duration={fade_dur}:offset={offset:.3f}{out_label}"
                )
                prev_label = out_label

            xfade_cmd = [
                'ffmpeg', '-y', *inputs,
                '-filter_complex', ";".join(filter_parts),
                '-map', '[outv]',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                '-pix_fmt', 'yuv420p', '-r', str(fps),
                output
            ]
            r = subprocess.run(xfade_cmd, capture_output=True, text=True, timeout=900)
            if r.returncode == 0:
                print(f"      ✓ Crossfade video ready")
                return output
            print(f"   ⚠ xfade failed (rc={r.returncode}), falling back to simple concat")
            print(f"   {r.stderr[-200:]}")

        # Simple concat of clips (no xfade, or xfade fallback)
        concat_file = os.path.join(temp_dir, "clips_list.txt")
        with open(concat_file, 'w') as f:
            for _, clip_path in ordered:
                f.write(f"file '{clip_path}'\n")
        concat_cmd = [
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
            '-c', 'copy', output
        ]
        r = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            print(f"      ✓ Clips concatenated")
            return output
        print(f"   ❌ Clip concat failed: {r.stderr[-200:]}")
        return None

    def compile(self, images: List[SceneImage], audio_path: str, output_path: str,
                intro_path: Optional[str] = None, intro_duration: float = 0) -> bool:
        """
        Compile images + audio into video.

        DESIGN: audio is law. One concat.txt covers 0 → audio_duration exactly.
        Every gap is filled with a black frame. Every missing image gets a
        placeholder card. No separate black-pad clip. No multi-step concatenation.
        """
        print(f"\n🎥 Compiling video...")
        temp_dir = tempfile.mkdtemp()
        try:
            # ── Get exact audio duration ──────────────────────────────────────────
            probe = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True, text=True)
            total_duration = float(probe.stdout.strip())
            print(f"   Audio: {total_duration:.3f}s  |  Images: {len(images)}")

            # ── Resolve missing images → placeholder cards ────────────────────────
            video_name   = os.path.splitext(os.path.basename(output_path))[0]
            workspace_dir = Path(output_path).parent.parent
            images_dir   = workspace_dir / 'images' / video_name
            images_dir.mkdir(parents=True, exist_ok=True)
            placeholders = 0
            for img in images:
                if not img.image_path or not os.path.exists(img.image_path):
                    ph = str(images_dir / f"placeholder_scene_{img.index:04d}.png")
                    self._generate_placeholder_image(img.index + 1, ph)
                    img.image_path = ph
                    placeholders += 1
            if placeholders:
                print(f"   {placeholders} missing images replaced with placeholder cards")

            # ── Black PNG for timeline gaps ───────────────────────────────────────
            black_img = os.path.join(temp_dir, "black.png")
            Image.new('RGB', (self.config.image_width, self.config.image_height),
                      (0, 0, 0)).save(black_img)
            black_abs = os.path.abspath(black_img).replace('\\', '/')

            # ── Ken Burns / crossfade path ────────────────────────────────────────
            use_clips = self.config.ken_burns or self.config.crossfade
            if use_clips:
                images_sorted = sorted(images, key=lambda x: x.start_time)
                images_video  = self._compile_clips(images_sorted, temp_dir)
                if not images_video:
                    return False
                print(f"   Image video ready (effects applied)")
                # For Ken Burns, still need to prepend the intro gap if any
                gap = images_sorted[0].start_time if images_sorted else 0.0
                if gap > 0.05:
                    gap_clip = os.path.join(temp_dir, "gap.mp4")
                    subprocess.run([
                        'ffmpeg', '-y', '-loop', '1', '-framerate', str(self.config.fps),
                        '-i', black_img, '-t', f'{gap:.6f}',
                        '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                        '-pix_fmt', 'yuv420p', '-r', str(self.config.fps), gap_clip
                    ], capture_output=True, text=True, timeout=60)
                    if os.path.exists(gap_clip):
                        gap_list = os.path.join(temp_dir, "gap_list.txt")
                        with open(gap_list, 'w') as f:
                            f.write(f"file '{gap_clip}'\n")
                            f.write(f"file '{os.path.abspath(images_video).replace(chr(92),'/')}'\n")
                        padded = os.path.join(temp_dir, "padded.mp4")
                        r = subprocess.run([
                            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', gap_list,
                            '-c', 'copy', padded
                        ], capture_output=True, text=True, timeout=300)
                        images_video = padded if r.returncode == 0 else images_video
                combined_video = images_video

            else:
                # ── Standard path: per-clip encode from prompts.json ──────────────
                prompts_path = workspace_dir / 'scripts' / f'{video_name}_prompts.json'

                def make_scene_clip(img_path: str, duration: float, clip_path: str) -> bool:
                    def _encode(path):
                        return subprocess.run([
                            'ffmpeg', '-y', '-loop', '1', '-framerate', str(self.config.fps),
                            '-i', path,
                            '-t', f'{duration:.6f}',
                            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                            '-pix_fmt', 'yuv420p', '-r', str(self.config.fps),
                            '-vf', 'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080',
                            clip_path
                        ], capture_output=True, text=True, timeout=600)
                    r = _encode(img_path)
                    if r.returncode != 0:
                        print(f"      FFmpeg error for {os.path.basename(img_path)}: {r.stderr[-300:].strip()}")
                        # Corrupt/invalid image — fall back to black frame so video stays full length
                        r = _encode(black_img)
                        if r.returncode == 0:
                            print(f"      ↳ Used black placeholder for {os.path.basename(img_path)}")
                    return r.returncode == 0

                if prompts_path.exists():
                    # ── prompts.json-driven per-clip encode (definitive sync fix) ──
                    with open(prompts_path, encoding='utf-8') as pf:
                        pdata = json.load(pf)
                    raw_scenes = pdata.get('scenes', pdata) if isinstance(pdata, dict) else pdata
                    ordered    = sorted(raw_scenes, key=lambda e: e['scene'])

                    # Build list of (img_path, duration, clip_path) jobs + gap clips
                    clips  = []   # final ordered list of clip paths
                    jobs   = []   # (img_path, duration, clip_path) to encode in parallel
                    cursor = 0.0

                    for idx, entry in enumerate(ordered):
                        m = re.match(r'([\d.]+)s\s*-\s*([\d.]+)s', entry.get('time', ''))
                        if not m:
                            continue
                        t0, t1 = float(m.group(1)), float(m.group(2))
                        if t1 <= t0:
                            continue

                        # Gap before this scene (intro gap or holes between scenes)
                        if t0 > cursor + 0.001:
                            gap_clip = os.path.join(temp_dir, f'gap_{idx:04d}.mp4')
                            jobs.append((black_img, t0 - cursor, gap_clip))
                            clips.append(gap_clip)

                        # Resolve image: real file → existing placeholder → new placeholder
                        snum     = entry['scene']
                        img_file = images_dir / f'scene_{snum - 1:04d}.png'
                        if not img_file.exists():
                            ph = images_dir / f'placeholder_scene_{snum - 1:04d}.png'
                            if not ph.exists():
                                self._generate_placeholder_image(snum, str(ph))
                            img_file = ph

                        clip_path = os.path.join(temp_dir, f'clip_{idx:04d}.mp4')
                        jobs.append((str(img_file), t1 - t0, clip_path))
                        clips.append(clip_path)
                        cursor = t1

                    # Trailing gap
                    tail = total_duration - cursor
                    if tail > 0.001:
                        tail_clip = os.path.join(temp_dir, 'tail.mp4')
                        jobs.append((black_img, tail, tail_clip))
                        clips.append(tail_clip)

                    print(f"   Encoding {len(jobs)} clips in parallel "
                          f"(workers={self.config.compile_workers})...")
                    failed = 0
                    with ThreadPoolExecutor(max_workers=self.config.compile_workers) as ex:
                        futures = {ex.submit(make_scene_clip, ip, dur, cp): cp
                                   for ip, dur, cp in jobs}
                        for fut in as_completed(futures):
                            if not fut.result():
                                failed += 1
                                print(f"   ⚠ clip encode failed: {futures[fut]}")
                    if failed:
                        print(f"   {failed}/{len(jobs)} clips failed to encode")

                    # Concat all clips with -c copy (no re-encode, exact frame counts)
                    clips_list = os.path.join(temp_dir, 'clips_list.txt')
                    with open(clips_list, 'w') as f:
                        for c in clips:
                            f.write(f"file '{os.path.abspath(c).replace(chr(92), '/')}'\n")

                    images_video = os.path.join(temp_dir, "images.mp4")
                    r = subprocess.run([
                        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', clips_list,
                        '-c', 'copy', images_video
                    ], capture_output=True, text=True, timeout=7200)

                    if r.returncode != 0:
                        print(f"   FFmpeg concat FAILED (rc={r.returncode})")
                        print(r.stderr[:1000] if len(r.stderr) > 1000 else r.stderr)
                        return False

                    print(f"   {len(clips)} clips encoded and joined")

                else:
                    # ── Fallback: original concat-demuxer path (no prompts.json) ──
                    concat_path = os.path.join(temp_dir, "concat.txt")
                    cursor      = 0.0
                    entries     = 0
                    images_sorted = sorted(images, key=lambda x: x.start_time)

                    with open(concat_path, 'w') as f:
                        for img in images_sorted:
                            if img.end_time <= cursor:
                                continue
                            effective_start = max(img.start_time, cursor)
                            effective_dur   = img.end_time - effective_start
                            gap = effective_start - cursor
                            if gap > 0.001:
                                f.write(f"file '{black_abs}'\nduration {gap:.6f}\n")
                            if effective_dur > 0:
                                abs_path = os.path.abspath(img.image_path).replace('\\', '/')
                                f.write(f"file '{abs_path}'\nduration {effective_dur:.6f}\n")
                                entries += 1
                                cursor = img.end_time
                        tail = total_duration - cursor
                        if tail > 0.001:
                            f.write(f"file '{black_abs}'\nduration {tail:.6f}\n")
                        f.write(f"file '{black_abs}'\n")

                    print(f"   Timeline: {entries} images, cursor={cursor:.3f}s, "
                          f"tail={max(0, total_duration-cursor):.3f}s")

                    if entries == 0:
                        print("   No images to compile!")
                        return False

                    images_video = os.path.join(temp_dir, "images.mp4")
                    r = subprocess.run([
                        'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_path,
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                        '-pix_fmt', 'yuv420p', '-r', str(self.config.fps),
                        '-vf', 'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080',
                        images_video
                    ], capture_output=True, text=True, timeout=7200)

                    if r.returncode != 0:
                        print(f"   FFmpeg concat FAILED (rc={r.returncode})")
                        print(r.stderr[:1000] if len(r.stderr) > 1000 else r.stderr)
                        return False

                combined_video = images_video

            # ── Mux with audio ────────────────────────────────────────────────────
            print(f"   Adding audio...")
            r = subprocess.run([
                'ffmpeg', '-y',
                '-i', combined_video,
                '-i', audio_path,
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                '-shortest',
                output_path
            ], capture_output=True, text=True, timeout=3600)

            if r.returncode != 0:
                print(f"   Audio mux failed: {r.stderr[-200:]}")
                shutil.copy(combined_video, output_path)

            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                probe2  = subprocess.run(
                    ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1', output_path],
                    capture_output=True, text=True)
                try:
                    final_dur = float(probe2.stdout.strip())
                    diff = final_dur - total_duration
                    print(f"\n   Video: {output_path}")
                    print(f"   Duration: {final_dur:.3f}s  (audio: {total_duration:.3f}s, diff: {diff:+.3f}s)")
                    print(f"   Size: {size_mb:.1f} MB")
                except Exception:
                    print(f"\n   Video: {output_path} ({size_mb:.1f} MB)")
                return True

            return False

        except Exception as e:
            print(f"   Error: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# =============================================================================
# MAIN AUTOMATION
# =============================================================================

class VideoAutomation:
    """Main automation orchestrator."""
    
    def __init__(self, api_key: str, workspace: str = "video_workspace", config: Config = None,
                 use_local_llm: bool = False, llm_provider: str = "ollama", llm_model: str = "llama3.2",
                 guidelines_path: str = None, use_saved_prompts: bool = False,
                 anthropic_api_key: str = None, claude_model: str = "claude-sonnet-4-6"):
        self.config = config or Config()
        self.workspace = Path(workspace)
        self.api_key = api_key

        # Find guidelines file
        if not guidelines_path:
            for gp in ["llm_guidelines.json", str(self.workspace / "llm_guidelines.json")]:
                if os.path.exists(gp):
                    guidelines_path = gp
                    break

        # Initialize local LLM if requested
        self.local_llm = None
        if use_local_llm:
            print("\n🤖 Initializing Local LLM for prompts...")
            self.local_llm = LocalLLMPromptGenerator(
                provider=llm_provider,
                model=llm_model,
                guidelines_path=guidelines_path
            )

        # Initialize Claude API batch generator if key provided
        self.claude_api_gen = None
        if anthropic_api_key:
            print("\n🤖 Initializing Claude API for batch prompt generation...")
            self.claude_api_gen = ClaudeAPIPromptGenerator(api_key=anthropic_api_key, model=claude_model)
        
        self.use_saved_prompts = use_saved_prompts
        self.generator = AI33Generator(api_key, self.config)
        self.parser = SegmentParser(self.config)
        self.prompt_builder = PromptBuilder(local_llm=self.local_llm)
        self.title_overlay = TitleBarOverlay(self.config)
        self.title_card_generator = NumberTitleCardGenerator(self.config)  # NEW
        self.compiler = VideoCompiler(self.config)
        
        # Create directories
        for d in ['characters', 'audio', 'images', 'videos', 'scripts', 'intro', 'style_references']:
            (self.workspace / d).mkdir(parents=True, exist_ok=True)
    
    def _find_character(self) -> Optional[str]:
        """
        Find character image in the characters folder.
        
        Priority:
        1. MC.png (exact name)
        2. Any other image file in the folder
        """
        char_dir = self.workspace / 'characters'
        if not char_dir.exists():
            char_dir.mkdir(parents=True, exist_ok=True)
            return None
        
        # First try exact names
        for name in ['MC.png', 'mc.png', 'MC.PNG', 'character.png', 'Character.png']:
            path = char_dir / name
            if path.exists():
                return str(path)
        
        # Then try any image file
        image_extensions = ['*.png', '*.PNG', '*.jpg', '*.JPG', '*.jpeg', '*.JPEG', '*.webp', '*.WEBP']
        for ext in image_extensions:
            images = list(char_dir.glob(ext))
            if images:
                return str(images[0])
        
        return None
    
    def _find_all_style_references(self) -> List[str]:
        """
        Find ALL image files in the style_references folder.
        
        - Accepts ANY filename (no naming pattern required)
        - Supports: PNG, JPG, JPEG, WEBP, BMP, GIF, TIFF
        - Case-insensitive extensions
        """
        style_dir = self.workspace / 'style_references'
        if not style_dir.exists():
            style_dir.mkdir(parents=True, exist_ok=True)
            return []
        
        refs = []
        
        # All common image extensions (case-insensitive)
        image_extensions = [
            '*.png', '*.PNG', 
            '*.jpg', '*.JPG', 
            '*.jpeg', '*.JPEG',
            '*.webp', '*.WEBP',
            '*.bmp', '*.BMP',
            '*.gif', '*.GIF',
            '*.tiff', '*.TIFF', '*.tif', '*.TIF'
        ]
        
        for ext in image_extensions:
            refs.extend([str(p) for p in style_dir.glob(ext)])
        
        # Remove duplicates (in case of case differences on Windows)
        refs = list(set(refs))
        
        # Shuffle so it's random from the start
        if refs:
            random.shuffle(refs)
        
        return refs
    
    def _find_intro(self) -> Optional[str]:
        """Find intro video."""
        intro_dir = self.workspace / 'intro'
        for ext in ['*.mp4', '*.mov']:
            videos = list(intro_dir.glob(ext))
            if videos:
                return str(videos[0])
        return None
    
    # ── Word-timestamp sync helpers ──────────────────────────────────────────
    _SYNC_STOPWORDS = {
        'the','a','an','is','it','in','of','to','and','or','was','were','they',
        'he','she','this','that','with','for','on','at','by','from','as','are',
        'be','been','has','had','have','not','but','what','all','we','when',
        'there','their','so','if','would','could','should','about','up','out',
        'no','just','him','his','her','can','do','did','its','now','even','than',
        'more','also','into','over','after','before','through','each','between',
        'who','which','where','how','then','these','those',
    }

    @staticmethod
    def _sync_key_words(text: str):
        import re
        tokens = re.findall(r"[a-zA-Z']+", text.lower())
        return [t for t in tokens
                if t not in VideoAutomation._SYNC_STOPWORDS and len(t) > 3]

    @staticmethod
    def _find_word_ts(word_entries: list, scene_text: str, search_from: float = 0.0):
        """Return the start timestamp of the first key word of scene_text."""
        keys = VideoAutomation._sync_key_words(scene_text)
        if not keys:
            return None
        for i, entry in enumerate(word_entries):
            if entry['start'] < search_from:
                continue
            w = entry['text'].lower().strip(".,!?;:'\"")
            if w == keys[0] or keys[0].startswith(w) or w.startswith(keys[0]):
                if len(keys) > 1:
                    for j in range(i + 1, min(i + 15, len(word_entries))):
                        w2 = word_entries[j]['text'].lower().strip(".,!?;:'\"")
                        if w2 == keys[1] or keys[1].startswith(w2) or w2.startswith(keys[1]):
                            return entry['start']
                else:
                    return entry['start']
        # fallback: first key word anywhere after search_from
        for entry in word_entries:
            if entry['start'] < search_from:
                continue
            w = entry['text'].lower().strip(".,!?;:'\"")
            if any(w == k or k.startswith(w) or w.startswith(k) for k in keys):
                return entry['start']
        return None

    def _apply_word_sync(self, content_images: list, word_entries: list,
                         total_duration: float):
        """Re-derive start times for all content images from word timestamps.

        Runs word-sync independently per segment so a false-positive match in
        one segment cannot advance the cursor into another segment's time range.
        """
        if not word_entries or not content_images:
            return 0

        # Group images by segment, preserving their current (zero-drift) order.
        by_segment: dict = {}
        for img in content_images:
            by_segment.setdefault(img.segment_number, []).append(img)

        timed = 0
        for seg_num, seg_imgs in sorted(by_segment.items(), reverse=True):
            # Sort this segment's images by current start_time (chronological).
            seg_imgs.sort(key=lambda x: x.start_time)
            seg_start = seg_imgs[0].start_time
            # Allow a small overrun past the last image's end_time so the final
            # word of a segment can still be matched.
            seg_end = seg_imgs[-1].end_time + 10.0

            cursor = seg_start
            for img in seg_imgs:
                if not img.text:
                    continue
                ts = self._find_word_ts(word_entries, img.text, search_from=cursor)
                # Reject matches that fall outside this segment's time window —
                # they are false positives caused by repeated words in the transcript.
                if ts is not None and ts <= seg_end:
                    img.start_time = ts
                    cursor = ts + 0.1
                    timed += 1

        if timed == 0:
            return 0
        # Sort all content images by updated start_times before stitching.
        content_images.sort(key=lambda x: x.start_time)
        # Rebuild contiguous end_times
        for i in range(len(content_images) - 1):
            nxt = content_images[i + 1].start_time
            content_images[i].end_time = nxt
            content_images[i].duration = nxt - content_images[i].start_time
        content_images[-1].end_time = total_duration
        content_images[-1].duration = total_duration - content_images[-1].start_time
        return timed
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_dhash(img_path: str, hash_size: int = 8) -> Optional[int]:
        """Compute a difference hash (dHash) for an image. Returns 64-bit int or None on failure."""
        try:
            img = Image.open(img_path).convert('L').resize(
                (hash_size + 1, hash_size), Image.LANCZOS)
            pixels = list(img.getdata())
            bits = 0
            for row in range(hash_size):
                for col in range(hash_size):
                    left  = pixels[row * (hash_size + 1) + col]
                    right = pixels[row * (hash_size + 1) + col + 1]
                    bits = (bits << 1) | (1 if left > right else 0)
            return bits
        except Exception:
            return None

    def _find_and_regen_duplicates(self, images: list, images_dir: Path,
                                    character_path: Optional[str]) -> int:
        """Scan generated images for near-duplicates and regenerate the later one in each pair."""
        print(f"\n🔍 Scanning for duplicate images (threshold={self.config.dupe_threshold})...")

        # Compute hashes for all done scenes
        hashes: dict[int, int] = {}  # scene.index -> dhash
        for img in images:
            if img.status == "done" and img.image_path and os.path.exists(img.image_path):
                h = self._compute_dhash(img.image_path)
                if h is not None:
                    hashes[img.index] = h

        # Find duplicate pairs
        indices = list(hashes.keys())
        to_regen: set[int] = set()
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                a, b = indices[i], indices[j]
                dist = bin(hashes[a] ^ hashes[b]).count('1')
                if dist <= self.config.dupe_threshold:
                    # Regen the later scene (higher index)
                    later = max(a, b)
                    to_regen.add(later)
                    print(f"   ↔ Scene {a+1} ≈ Scene {b+1} (dist={dist}) → regen scene {later+1}")

        if not to_regen:
            print("   ✓ No duplicates found")
            return 0

        print(f"   Regenerating {len(to_regen)} duplicate scene(s)...")
        regenned = 0
        for img in images:
            if img.index in to_regen:
                out_path = str(images_dir / f"scene_{img.index:04d}.png")
                if os.path.exists(out_path):
                    os.remove(out_path)
                idx, success, credits = self._generate_single_image(img, character_path, images_dir)
                if success:
                    regenned += 1
                    print(f"   ✓ Regenerated scene {idx+1}")
                else:
                    print(f"   ✗ Failed to regenerate scene {idx+1}")
        return regenned

    def _generate_single_image(self, scene: SceneImage, character_path: Optional[str],
                                output_dir: Path) -> Tuple[int, bool, int]:
        """Generate a single image. Returns (index, success, credits)."""
        output_path = str(output_dir / f"scene_{scene.index:04d}.png")

        # Regen-scenes: force-delete existing file so it gets re-generated
        if self.config.regen_scenes and (scene.index + 1) in self.config.regen_scenes:
            if os.path.exists(output_path):
                os.remove(output_path)
                print(f"      🔄 Regen scene {scene.index + 1}: deleted existing file")

        # Skip if already generated (allows re-runs for compile-only fixes)
        if os.path.exists(output_path):
            try:
                with Image.open(output_path) as _img:
                    _img.load()
                scene.image_path = output_path
                scene.status = "done"
                return scene.index, True, 0
            except Exception:
                os.remove(output_path)
                print(f"      🔧 Corrupt existing file deleted, regenerating scene {scene.index + 1}")

        # Compile-only mode: don't call API for missing images
        if self.config.compile_only:
            return scene.index, False, 0

        prompt = self.prompt_builder.build(scene)
        
        # Get rotating style reference
        style_path = None
        if self.config.use_style_reference and hasattr(self, 'style_manager'):
            style_path = self.style_manager.get_next()
        
        # Character handling:
        # - If scene includes character: send MC as @img2 and include in image
        # - If scene doesn't include character: still send MC as reference so AI knows 
        #   what to REMOVE/REPLACE from style references that have stick figures
        
        char_for_scene = character_path if scene.include_character else None
        mc_reference = character_path  # Always send MC so AI knows what it looks like
        
        last_error = ""
        for attempt in range(self.config.max_retries):
            success, error, credits = self.generator.generate(
                prompt, output_path,
                character_path=char_for_scene,
                style_ref_path=style_path,
                always_include_mc=mc_reference if not char_for_scene else None,
                negative_prompt=scene.negative_prompt
            )
            
            if success:
                scene.image_path = output_path
                scene.status = "done"
                
                # Add title bar
                self.title_overlay.add_title(output_path, scene.segment_title)
                
                return scene.index, True, credits
            
            last_error = error
            if attempt < self.config.max_retries - 1:
                print(f"      ⚠ Retry {attempt + 2}/{self.config.max_retries}: {error[:50]}")
                time.sleep(3)
        
        # Print final error
        print(f"      ❌ Error: {last_error}")
        scene.status = "failed"
        return scene.index, False, 0
    
    def process(self, audio_path: str, video_name: str, video_title: str = "", 
                transcript_file: str = None) -> Optional[str]:
        """Process audio into video."""
        print("\n" + "="*60)
        print("🎬 VIDEO AUTOMATION V2 - Segment Based")
        print(f"   Model: {self.config.ai33_model}")
        if self.local_llm and self.local_llm.enabled:
            print(f"   Local LLM: ✓ Enabled")
        print("="*60)
        
        # Find assets
        character_path = self._find_character()
        style_refs = self._find_all_style_references()
        intro_path = self._find_intro()
        
        # Create style reference manager
        self.style_manager = StyleReferenceManager(style_refs, cooldown=5)
        
        print(f"\n📁 Assets:")
        print(f"   Character:   {'✓ ' + os.path.basename(character_path) if character_path else '✗ Not found'}")
        print(f"   Style Refs:  {'✓ ' + str(len(style_refs)) + ' images (rotating, 5 cooldown)' if style_refs else '✗ Not found'}")
        print(f"   Intro:       {'✓ Found' if intro_path else '✗ Not found'}")
        
        if style_refs:
            for i, ref in enumerate(style_refs[:5]):  # Show first 5
                print(f"      [{i+1}] {os.path.basename(ref)}")
            if len(style_refs) > 5:
                print(f"      ... and {len(style_refs) - 5} more")
        
        # Load transcript - strictly fail-fast word timestamps
        _auto_transcript_path = os.path.join(str(self.workspace), "scripts", f"{video_name}_word_timestamps.json")
        _use_existing = (
            (transcript_file and str(transcript_file).strip() not in ("0", "false", ""))
            or (self.config.compile_only and os.path.exists(_auto_transcript_path))
        )
        if not _use_existing:
            print("Starting Faster-Whisper transcription...")
            
            if not os.path.exists(audio_path):
                raise RuntimeError(f"Audio not found: {audio_path}")
                
            transcriber = FasterWhisperTranscriber()
            full_text, word_data, total_duration = transcriber.transcribe(audio_path)

            # Release Whisper model from GPU immediately so Ollama has VRAM for LLM
            transcriber.model = None
            del transcriber
            try:
                import torch
                torch.cuda.empty_cache()
                print("   ✓ Whisper model released from GPU memory.")
            except Exception:
                pass

            if not word_data or len(word_data) == 0:
                raise RuntimeError("Transcription returned empty word data.")

            transcript_path = os.path.join(
                str(self.workspace),
                "scripts",
                f"{video_name}_word_timestamps.json"
            )

            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump({
                    "text": full_text,
                    "entries": word_data,
                    "duration": total_duration
                }, f, indent=2, ensure_ascii=False)

            print("Transcription complete. Word-level timestamps saved.")
            entries = word_data
            
        else:
            transcript_path = _auto_transcript_path
            if self.config.compile_only and os.path.exists(transcript_path):
                print("Compile-only mode: reusing existing word-level transcript...")
            else:
                print("Loading existing word-level transcript...")

            if not os.path.exists(transcript_path):
                raise RuntimeError("Transcript file missing but Transcript=1.")
                
            with open(transcript_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                entries = data.get("entries", [])
                total_duration = data.get("duration", 0.0)
                full_text = data.get("text", "")
        
        # Parse segments from entries - strictly expects word_data
        print(f"\n📍 Parsing segments...")
        _manifest_path = os.path.join(str(self.workspace), "scripts", f"{video_name}_segments_manifest.json")
        segments, intro_audio_duration = self.parser.parse_segments(entries, total_duration, manifest_path=_manifest_path)
        
        # Full transcript text for LLM
        if not full_text:
            full_text = ' '.join(e['text'] for e in entries)
        
        # Setup output directory
        images_dir = self.workspace / 'images' / video_name
        images_dir.mkdir(parents=True, exist_ok=True)
        
        # Title cards have been removed per user specs.
        # We now generate images for the ENTIRE segment duration and add title overlays to them.
        
        # STEP 2: Plan CONTENT images (use LLM or Python)
        content_images = []
        
        # LLM scene planning is disabled — small local models (3b) cannot reliably
        # produce valid timestamps for the full audio duration and consistently
        # hallucinate out-of-range values. Python-based planning is used instead,
        # and the LLM is only called for per-scene prompt text generation.
        llm_scenes = None

        if llm_scenes:
            # Convert LLM scenes to SceneImage objects
            print(f"\n📋 LLM Content Scenes:")
            for i, scene in enumerate(llm_scenes):
                start_time = scene.get('start_time', 0)
                end_time = scene.get('end_time', start_time + 5)
                duration = end_time - start_time
                
                # Find which segment this belongs to
                seg_number = 0
                seg_title = "Content"
                for seg in segments:
                    if start_time >= seg.start_time:
                        seg_number = seg.number
                        seg_title = seg.title
                
                img = SceneImage(
                    index=len(content_images),
                    segment_number=seg_number,
                    segment_title=seg_title,
                    text=scene.get('spoken_text', '')[:200],
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    include_character=scene.get('include_character', False)
                )
                img.llm_prompt = scene.get('prompt', '')
                img.is_title_card = False
                content_images.append(img)
                
                print(f"   {len(content_images)}. @ {start_time:.1f}s ({duration:.1f}s) - {scene.get('prompt', '')[:50]}...")
        
        else:
            # Fall back to Python-based content planning
            print(f"\n📍 Using Python-based content planning...")
            
            idx = 0
            for seg in segments:
                # Use the full segment duration
                temp_seg = Segment(
                    number=seg.number,
                    title=seg.title,
                    text=seg.text,
                    start_time=seg.start_time,
                    end_time=seg.end_time,
                    entries=seg.entries
                )
                
                images = self.parser.create_images_for_segment(temp_seg, idx)
                for img in images:
                    img.is_title_card = False
                content_images.extend(images)
                idx += len(images)
        
        # STEP 3: Mark "Number X" scenes as title cards in-place (no separate insertion)
        # The content scene whose text starts with "Number <digit>" IS the number card moment.
        import re as _re
        for img in content_images:
            if _re.match(r'^Number\s+\d+', img.text.strip(), _re.IGNORECASE):
                img.is_title_card = True
                print(f"🪧 Marked NI card | seg #{img.segment_number} | {img.start_time:.2f}s → {img.end_time:.2f}s")

        all_images = content_images

        all_images.sort(key=lambda x: x.start_time)
        for i, img in enumerate(all_images):
            img.index = i

        # STRICT ZERO-DRIFT ENFORCEMENT
        # The intro covers from 0 to intro_audio_duration.
        # The images must perfectly span from intro_audio_duration to total_duration.
        print("\n📏 Enforcing Zero Drift (Contiguous Timeline)...")
        if all_images:
            # Force first image to start exactly at intro end
            if abs(all_images[0].start_time - intro_audio_duration) > 0.05:
                all_images[0].start_time = intro_audio_duration
                all_images[0].duration = all_images[0].end_time - all_images[0].start_time
                
            # Make sure every image connects perfectly to the next
            for i in range(len(all_images) - 1):
                img = all_images[i]
                next_img = all_images[i+1]
                
                # If they don't perfectly align, force the current image to stretch/shrink to the next start
                img.end_time = next_img.start_time
                img.duration = img.end_time - img.start_time
                
            # Last image goes until audio ends
            all_images[-1].end_time = total_duration
            all_images[-1].duration = total_duration - all_images[-1].start_time
            
            # Remove any zero or negative duration images that got squashed
            all_images = [img for img in all_images if img.duration > 0.05]
        
        # Build strict timeline JSON output
        timeline_json = []
        for img in all_images:
            timeline_json.append({
                "type": "number_card" if img.is_title_card else "content",
                "start": img.start_time,
                "end": img.end_time,
                "duration": img.duration,
                "prompt": img.text if img.is_title_card else img.llm_prompt,
                "number": img.segment_number if img.is_title_card else None
            })
            
        timeline_path = self.workspace / 'scripts' / f'{video_name}_timeline.json'
        with open(timeline_path, 'w', encoding='utf-8') as f:
            json.dump(timeline_json, f, indent=2)
        print(f"\n   ✓ Saved structured timeline: {timeline_path}")
        
        # Re-index
        for i, img in enumerate(all_images):
            img.index = i

        # Auto word-sync: re-derive each content scene's start time from the
        # exact word in the transcript where that concept is first spoken.
        # Skip when use_saved_prompts is on — prompts.json timestamps will
        # override the timeline later (after LLM prompt loading at STEP 4).
        if not self.use_saved_prompts:
            content_imgs_for_sync = [img for img in all_images if not img.is_title_card]
            synced = self._apply_word_sync(content_imgs_for_sync, entries, total_duration)
            if synced:
                print(f"\n🔄 Auto word-sync: {synced} scene cuts aligned to word timestamps")
                # Re-sort all images (including title cards) by their updated start_times.
                # Word-sync shifts some content images forward; without re-sorting, the
                # stitching loop below produces negative durations and crushes NI cards to 0s.
                all_images.sort(key=lambda x: x.start_time)
                # Re-stitch ALL image boundaries (including title cards) so the NI card extends
                # to fill any gap between its original end and content[0]'s new word-synced start.
                # Without this, the NI card ends early and all content images play ~0.92s too soon.
                for i in range(len(all_images) - 1):
                    all_images[i].end_time = all_images[i + 1].start_time
                    all_images[i].duration = all_images[i].end_time - all_images[i].start_time
        else:
            print(f"   ⏭ Skipping word sync — prompts.json timestamps will be applied at STEP 4")

        # ── SCENE TEXT ONLY MODE ──────────────────────────────────────────────────
        if self.config.scene_text_only:
            content_scenes = [img for img in all_images if not img.is_title_card]
            scene_data = []
            for img in content_scenes:
                scene_data.append({
                    "scene": img.index + 1,
                    "segment": f"#{img.segment_number} {img.segment_title}",
                    "time": f"{img.start_time:.2f}s - {img.end_time:.2f}s",
                    "duration_s": round(img.duration, 2),
                    "scene_text": img.text,
                    "prompt": "",
                    "negative": ""
                })

            scene_json_path = self.workspace / 'scripts' / f'{video_name}_scene_texts.json'
            with open(scene_json_path, 'w', encoding='utf-8') as f:
                json.dump(scene_data, f, indent=2, ensure_ascii=False)
            print(f"\n✅ Scene texts saved → {scene_json_path}")

            # Collect style reference info
            style_refs = self._find_all_style_references()
            style_ref_section = []
            if style_refs:
                style_ref_section = [
                    "=" * 70,
                    "STYLE REFERENCES",
                    "=" * 70,
                    f"The user has {len(style_refs)} style reference image(s) that will be fed to the",
                    "image model alongside every prompt. Describe a visual style that is CONSISTENT",
                    "with these references (e.g. same color grading, lighting mood, art direction).",
                    "When writing prompts, explicitly reference that visual style rather than",
                    "describing a completely different aesthetic.",
                    "",
                ]

            lines = [
                "=" * 70,
                "ROLE",
                "=" * 70,
                "You are an expert art director writing image generation prompts for a YouTube",
                "video. The visual style is a COMPOSITE: photorealistic deep space photography",
                "as the background layer, with flat 2D cartoon illustrations layered on top.",
                "Each prompt is sent independently to an AI image model — the model sees NO",
                "context between scenes, so every prompt must be fully self-contained.",
                "",
                "=" * 70,
                "VIDEO INFO",
                "=" * 70,
                f"Title:        {video_title or video_name}",
                f"Total scenes: {len(content_scenes)}",
                f"Segments:     {len(segments)}",
                "Format:       16:9 widescreen, composite style (photorealistic space background + 2D cartoon overlay)",
                "",
                *style_ref_section,
                "=" * 70,
                "MANDATORY ART STYLE — READ THIS BEFORE WRITING ANY PROMPT",
                "=" * 70,
                "BACKGROUND LAYER (every scene): Real-photography deep space — deep navy to black",
                "with visible purple-pink nebula clouds, dense star fields, faint galaxy spirals.",
                "This is photorealistic, NOT illustrated.",
                "",
                "CARTOON LAYER (every scene): Flat 2D cartoon elements rendered over the space background.",
                "  • Bold black outlines on all cartoon shapes",
                "  • NO gradients inside cartoon shapes — clean cel-shaded flat color fills only",
                "  • Characters: expressive stick figures, large round white heads, dot/circular eyes,",
                "    simple mouths, wearing recognizable clothing with personality and clear emotion",
                "  • Objects/props: detailed cartoon illustrations with bold black outlines and flat fills",
                "  • Cartoon elements float naturally over the photorealistic background as a composite",
                "",
                "DOMINANT CARTOON PALETTE: golden yellow, coral red, violet purple, warm orange, clean white",
                "SCALE CONTRAST: dramatic — tiny cartoon figures against vast cosmic backgrounds",
                "EXTRAS (use when relevant): thought bubbles, speech bubbles, symbolic icons for ideas",
                "WARM GLOWING ACCENTS on key cartoon elements",
                "",
                "=" * 70,
                "STEP 0 — WRITE A VISUAL BIBLE FIRST  (before any scene prompts)",
                "=" * 70,
                "Before writing a single scene prompt, write a short visual bible for this video.",
                "It must define:",
                "  • The photorealistic space background palette for this video (nebula color temperature,",
                "    star density, galaxy spiral visibility — be specific)",
                "  • Which cartoon palette colors dominate (from: golden yellow, coral red, violet purple,",
                "    warm orange, clean white) — choose 2-3 primaries for this video's tone",
                "  • Any recurring cartoon characters and their exact stick-figure description",
                "    (clothing color, expression style, accessories)",
                "  • Any recurring cartoon objects/symbols specific to this video's topic",
                "  • What this video should NEVER look like (e.g. no 3D rendering, no anime style,",
                "    no gradients inside cartoon shapes, no realistic human faces, no text labels)",
                "You will reference this bible throughout the entire session.",
                "Include it in the output JSON as the 'visual_bible' key (see OUTPUT FORMAT).",
                "",
                "=" * 70,
                "WHAT MAKES A GREAT PROMPT (for this composite style)",
                "=" * 70,
                "Structure every prompt with TWO distinct layers:",
                "",
                "BACKGROUND: Describe the photorealistic space backdrop — nebula colors, star density,",
                "  galaxy spiral details, depth. E.g. 'deep navy to black photorealistic space",
                "  background, dense cold white star field, purple-pink nebula wisps at left edge,",
                "  faint spiral galaxy suggestion in upper right'",
                "",
                "FOREGROUND: Describe the cartoon element(s) floating over the space background —",
                "  1. SUBJECT  — what flat 2D cartoon object/character is in the scene; describe",
                "                 color fills, outline weight, expression (for characters)",
                "  2. ACTION   — what the cartoon element is doing or implying",
                "  3. SCALE    — how large relative to the frame (tiny figure = ~8-15% frame height,",
                "                 dominant object = ~40-60% frame width)",
                "  4. POSITION — where in the 16:9 frame (center, left-third, floating upper-right, etc.)",
                "  5. EXTRAS   — thought bubble, speech bubble, or symbolic icon if narratively useful",
                "  6. ACCENTS  — warm glowing halo, glow outline, or accent light on key elements",
                "",
                "7. MOOD     — the emotional tone of the composite (vast/lonely, energetic, mysterious)",
                "8. NEGATIVE — what to exclude: 3D rendering, gradients inside cartoon shapes, anime style,",
                "              realistic human faces, text labels, watermarks, blurry cartoon outlines",
                "",
                "SCENE DURATION GUIDE (adjust complexity to match screen time):",
                "  < 5s  — Simple: one clear cartoon element, bold and readable, centered or near-center.",
                "  5-9s  — Standard: cartoon subject + action + supporting detail in background.",
                "  > 9s  — Rich: layered cartoon scene, multiple elements, detailed space backdrop.",
                "",
                "=" * 70,
                "SELF-IMPROVING LOOP  (mandatory for every segment)",
                "=" * 70,
                "BEFORE writing the first scene of each segment:",
                "  1. Re-read the segment title and ALL its scene texts together.",
                "  2. Decide the single strongest cartoon visual concept for the segment.",
                "  3. Pick which cartoon palette colors dominate this segment's emotional tone.",
                "  4. Decide the nebula/star backdrop variation to use (denser, sparser, warmer, cooler).",
                "",
                "AFTER completing every segment:",
                "  1. REVIEW: re-read each prompt you just wrote.",
                "  2. SCORE: Strong (vivid, specific cartoon element over specific space backdrop) /",
                "            OK / Weak (vague cartoon shape, generic 'space background').",
                "  3. IDENTIFY weak patterns: 'floating in space', 'vibrant colors', 'dramatic scene' — ban these.",
                "  4. IMPROVE: specify exact cartoon outline style, exact nebula hue, exact glow color.",
                "  5. VARY: alternate which cartoon palette color dominates, vary figure scale and position.",
                "     No composition should repeat more than twice in a row.",
                "",
                "CONSISTENCY LEDGER (build this as you go):",
                "  • Lock in cartoon character clothing colors at first mention; repeat exactly every time.",
                "  • Lock in the nebula palette variation used for each major segment; stay consistent.",
                "  • Keep the bold-black-outline cartoon style constant — never let it drift to soft or sketchy.",
                "",
                "=" * 70,
                "PROCESS  (step by step — do not skip any step)",
                "=" * 70,
                "Step 0: Write your visual bible.",
                "Step 1: Read ALL scene texts to understand the full narrative arc.",
                "Step 2: Identify recurring characters, key locations, and emotional climax scenes.",
                "Step 3: For each scene — draft → self-critique → refine — before moving on.",
                "Step 4: After each segment boundary — score, identify weaknesses, plan improvements.",
                "Step 5: Output the final JSON object.",
                "",
                "=" * 70,
                "OUTPUT FORMAT",
                "=" * 70,
                "Return ONLY a valid JSON object — no explanation, no markdown fences, no extra text.",
                "Format:",
                "{",
                '  "visual_bible": "Your visual bible here...",',
                '  "scenes": [',
                '    {"scene": 1, "prompt": "...", "negative": "3D rendering, gradients inside cartoon shapes, anime style, realistic human faces, text labels, watermarks, blurry cartoon outlines, smooth shading on cartoon elements"},',
                '    {"scene": 2, "prompt": "...", "negative": "..."},',
                "    ...",
                "  ]",
                "}",
                "",
                f"The user will save this as:  video_workspace/scripts/{video_name}_prompts.json",
                "and re-run the pipeline with 'Load prompts from JSON' checked.",
                "",
                "=" * 70,
                "SCENES",
                "=" * 70,
                "",
            ]
            prev_segment = None
            for entry in scene_data:
                seg_label = entry['segment']
                dur = entry['duration_s']
                if seg_label != prev_segment:
                    if prev_segment is not None:
                        lines.append("")
                    lines.append(f"── SEGMENT: {seg_label} " + "─" * max(0, 50 - len(seg_label)))
                    prev_segment = seg_label
                complexity = "Simple/bold" if dur < 5 else ("Standard" if dur < 9 else "Rich/detailed")
                lines.append(f"  Scene {entry['scene']}  [{entry['time']}  {dur}s — {complexity}]")
                lines.append(f"  {entry['scene_text']}")
                lines.append("")

            prompt_path = self.workspace / 'scripts' / f'{video_name}_claude_batch_prompt.txt'
            with open(prompt_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))
            print(f"📋 Claude batch prompt → {prompt_path}")
            print(f"\nNext steps:")
            print(f"  1. Open {prompt_path}")
            print(f"  2. Paste into Claude and get the JSON object back")
            print(f"  3. Save as: video_workspace/scripts/{video_name}_prompts.json")
            print(f"  4. Re-run with 'Load prompts from JSON' checked")
            return True
        # ─────────────────────────────────────────────────────────────────────────

        print(f"\n📊 Summary:")
        print(f"   Intro duration: {intro_audio_duration:.2f}s")
        print(f"   Content images to generate: {len(all_images)}")
        print(f"   Total scenes: {len(all_images)}")
        for seg in segments:
            seg_images = [i for i in all_images if i.segment_number == seg.number]
            print(f"      #{seg.number}: '{seg.title}' @ {seg.start_time:.2f}s → {len(seg_images)} images")
        
        # STEP 4: Pre-generate LLM prompts sequentially before parallel image generation
        images_to_generate = all_images
        time_applied = 0  # tracks how many exact timestamps were loaded from prompts.json

        if self.use_saved_prompts:
            prompts_path = self.workspace / 'scripts' / f'{video_name}_prompts.json'
            if prompts_path.exists():
                with open(prompts_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                # Support both old array format and new dict format {"visual_bible":..., "scenes":[...]}
                if isinstance(saved, dict):
                    visual_bible = saved.get('visual_bible', '')
                    if visual_bible:
                        print(f"\n📖 Visual bible loaded ({len(visual_bible)} chars)")
                    scenes_list = saved.get('scenes', [])
                else:
                    scenes_list = saved
                saved_ordered = sorted(scenes_list, key=lambda e: e['scene'])

                # ── Inject any number cards from prompts.json that segment parser missed ──
                saved_cards   = [e for e in saved_ordered if e.get('type') == 'number_card']
                saved_content = [e for e in saved_ordered if e.get('type') != 'number_card']
                if saved_cards:
                    injected = 0
                    for entry in saved_cards:
                        m = re.match(r'([\d.]+)s\s*-\s*([\d.]+)s', entry.get('time', ''))
                        if not m:
                            continue
                        t0, t1 = float(m.group(1)), float(m.group(2))
                        # Skip if segment parser already created a title card near this time
                        if any(img.is_title_card and abs(img.start_time - t0) < 0.5
                               for img in images_to_generate):
                            continue
                        nm = re.search(r'\d+', entry.get('scene_text', ''))
                        num_val   = int(nm.group()) if nm else 0
                        seg_raw   = entry.get('segment', '')
                        seg_title = re.sub(r'^#\d+\s*', '', seg_raw).strip()
                        new_card  = SceneImage(
                            index=0,
                            segment_number=num_val,
                            segment_title=seg_title,
                            text=entry.get('scene_text', ''),
                            start_time=t0,
                            end_time=t1,
                            duration=t1 - t0,
                            include_character=False,
                            is_title_card=True,
                        )
                        images_to_generate.append(new_card)
                        injected += 1
                    if injected:
                        images_to_generate.sort(key=lambda x: (x.start_time, 0 if x.is_title_card else 1))
                        for i, img in enumerate(images_to_generate):
                            img.index = i
                        print(f"   ✚ Injected {injected} number card(s) from prompts.json")
                else:
                    saved_content = saved_ordered  # no cards — use full list as before

                content_scenes = [img for img in images_to_generate if not img.is_title_card]
                loaded = 0
                time_applied = 0
                short_prompts = []
                long_prompts = []
                for idx, entry in enumerate(saved_content):
                    if idx >= len(content_scenes):
                        break
                    content_scenes[idx].llm_prompt = entry['prompt']
                    content_scenes[idx].negative_prompt = entry.get('negative', '')
                    loaded += 1
                    word_count = len(entry['prompt'].split())
                    if word_count < 40:
                        short_prompts.append((idx + 1, word_count))
                    elif word_count > 200:
                        long_prompts.append((idx + 1, word_count))
                    # Apply exact timestamps from prompts.json for precise sync
                    m = re.match(r'([\d.]+)s\s*-\s*([\d.]+)s', entry.get('time', ''))
                    if m:
                        t0, t1 = float(m.group(1)), float(m.group(2))
                        if t1 > t0:
                            content_scenes[idx].start_time      = t0
                            content_scenes[idx].end_time        = t1
                            content_scenes[idx].duration        = t1 - t0
                            content_scenes[idx].audio_lock_time = t0
                            time_applied += 1

                print(f"\n📂 Loaded {loaded}/{len(content_scenes)} saved prompts from {prompts_path}")
                if short_prompts:
                    print(f"   ⚠ Short prompts (<40 words): scenes {[s[0] for s in short_prompts]}")
                if long_prompts:
                    print(f"   ⚠ Long prompts (>200 words): scenes {[s[0] for s in long_prompts]} — may be truncated by model")

                if time_applied > 0:
                    print(f"   ⏱ Applied {time_applied} exact timestamps from prompts.json — re-stitching number cards...")
                    # Re-sort: title cards BEFORE content scenes at equal start_time so the
                    # re-stitch sets their end_time = same start_time → 0-duration → filtered.
                    images_to_generate.sort(key=lambda x: (x.start_time, 0 if x.is_title_card else 1))
                    # Only re-stitch TITLE CARDS (number cards).
                    # Content scenes already have correct start AND end from prompts.json —
                    # chaining all images together would corrupt number card end_times when
                    # any content scene's timestamp doesn't perfectly neighbour its card.
                    for i in range(len(images_to_generate) - 1):
                        img = images_to_generate[i]
                        if img.is_title_card:
                            next_start = images_to_generate[i + 1].start_time
                            img.end_time = next_start
                            img.duration = next_start - img.start_time
                    # Last image fills to end of audio
                    images_to_generate[-1].end_time  = total_duration
                    images_to_generate[-1].duration  = total_duration - images_to_generate[-1].start_time
                    # Drop any that got squashed to zero
                    images_to_generate[:] = [img for img in images_to_generate if img.duration > 0.05]
                    # Re-index
                    for i, img in enumerate(images_to_generate):
                        img.index = i
                    print(f"   ✓ Timeline locked to prompts.json timestamps ({images_to_generate[0].start_time:.2f}s → {images_to_generate[-1].end_time:.2f}s)")
            else:
                print(f"\n⚠ --use-saved-prompts: file not found at {prompts_path}, continuing without prompts")

        elif self.local_llm and self.local_llm.enabled:
            content_scenes = [img for img in images_to_generate if not img.is_title_card]
            print(f"\n🤖 Pre-generating {len(content_scenes)} prompts with LLM...")
            print(f"   Warming up model (first load may take 30-60s)...")
            try:
                requests.post(f"{self.local_llm.base_url}/api/generate",
                    json={"model": self.local_llm.model, "prompt": "ready", "stream": False,
                          "options": {"num_predict": 1}}, timeout=300)
                print(f"   Model ready.")
            except Exception as e:
                print(f"   Warmup warning: {e}")
            seg_text_map = {seg.number: seg.text for seg in segments}
            for i, img in enumerate(content_scenes):
                if img.llm_prompt:
                    continue
                seg_text = seg_text_map.get(img.segment_number, "")
                prompt = self.local_llm.generate_prompt(
                    img.text, img.segment_title, img.include_character, segment_text=seg_text
                )
                if prompt:
                    img.llm_prompt = prompt
                    print(f"   {i+1}/{len(content_scenes)} #{img.segment_number} prompt ready ({len(prompt)} chars)")
                else:
                    print(f"   {i+1}/{len(content_scenes)} #{img.segment_number} LLM failed, will use template")

            # Save all prompts to disk for review
            prompts_path = self.workspace / 'scripts' / f'{video_name}_prompts.json'
            prompt_log = []
            for img in content_scenes:
                prompt_log.append({
                    "scene": img.index + 1,
                    "segment": f"#{img.segment_number} {img.segment_title}",
                    "time": f"{img.start_time:.2f}s - {img.end_time:.2f}s",
                    "scene_text": img.text,
                    "prompt_source": "llm" if img.llm_prompt else "template",
                    "prompt": img.llm_prompt or img.text
                })
            with open(prompts_path, 'w', encoding='utf-8') as f:
                json.dump(prompt_log, f, indent=2, ensure_ascii=False)
            print(f"   ✓ Prompts saved → {prompts_path}")

            if self.config.prompts_only:
                print(f"\n✅ Prompts-only mode: stopping here. Edit {prompts_path} then re-run with 'Load prompts from JSON'.")
                return True

        elif self.claude_api_gen and self.claude_api_gen.enabled:
            content_scenes  = list(images_to_generate)
            seg_text_map    = {seg.number: seg.text for seg in segments}
            print(f"\n🤖 Batch-generating {len(content_scenes)} prompts via Claude API...")

            results = self.claude_api_gen.generate_all_prompts(content_scenes, seg_text_map)
            for img in content_scenes:
                if img.index in results:
                    img.llm_prompt = results[img.index]

            # Save prompts.json for review / future use
            prompts_path = self.workspace / 'scripts' / f'{video_name}_prompts.json'
            prompt_log   = []
            for img in content_scenes:
                prompt_log.append({
                    "scene":        img.index + 1,
                    "segment":      f"#{img.segment_number} {img.segment_title}",
                    "time":         f"{img.start_time:.2f}s - {img.end_time:.2f}s",
                    "scene_text":   img.text,
                    "prompt_source": "claude-api" if img.llm_prompt else "template",
                    "prompt":       img.llm_prompt or img.text
                })
            with open(prompts_path, 'w', encoding='utf-8') as f:
                json.dump(prompt_log, f, indent=2, ensure_ascii=False)
            print(f"   ✓ Prompts saved → {prompts_path}")

            if self.config.prompts_only:
                print(f"\n✅ Prompts-only mode: stopping here. Edit {prompts_path} then re-run with 'Load prompts from JSON'.")
                return True

        # Regen-scene fast path: when --regen-scenes is specified, pre-mark all
        # existing non-regen images as done so the thread pool only processes
        # the scenes that actually need a new API call.
        if self.config.regen_scenes:
            regen_set = set(self.config.regen_scenes)  # 1-based scene numbers
            for img in all_images:
                snum = img.index + 1  # convert 0-based index → 1-based
                if snum not in regen_set:
                    op = str(images_dir / f"scene_{img.index:04d}.png")
                    if os.path.exists(op):
                        try:
                            with Image.open(op) as _chk:
                                _chk.load()
                            img.image_path = op
                            img.status = "done"
                        except Exception:
                            pass  # corrupt — thread pool will regenerate it
            images_to_generate = [img for img in images_to_generate
                                   if (img.index + 1) in regen_set]
            print(f"\n🔄 Regen mode: {len(images_to_generate)} scene(s) → {sorted(regen_set)}")

        # Generate images in parallel
        print(f"\n🎨 Generating {len(images_to_generate)} content images ({self.config.max_workers} parallel)...")
        if style_refs:
            print(f"   🔄 Rotating through {len(style_refs)} style references")
        
        completed = 0
        failed = 0
        total_credits = 0
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(
                    self._generate_single_image, img, character_path, images_dir
                ): img
                for img in images_to_generate
            }
            
            for future in as_completed(futures):
                img = futures[future]
                try:
                    idx, success, credits = future.result()
                    total_credits += credits
                    
                    if success:
                        completed += 1
                        char_marker = " +MC" if img.include_character else ""
                        print(f"   ✓ {completed}/{len(images_to_generate)} - #{img.segment_number} '{img.segment_title}'{char_marker} ({credits} cr)")
                    else:
                        failed += 1
                        print(f"   ✗ Scene {idx+1} FAILED")
                        
                except Exception as e:
                    failed += 1
                    print(f"   ✗ Error: {e}")
        
        print(f"\n📊 Generation Complete:")
        print(f"   Content images: {completed}/{len(images_to_generate)}")
        print(f"   Failed: {failed}")
        print(f"   Total AI Credits: {total_credits:,}")

        # Duplicate detection — scan for visually similar images and regenerate them
        if self.config.find_dupes and not self.config.compile_only:
            self._find_and_regen_duplicates(all_images, images_dir, character_path)
        
        # When using saved prompts, content scenes cover the full timeline exactly.
        # Number cards from parse_segments() have unreliable start_times that overlap
        # with content scenes and cause cumulative drift — exclude them entirely.
        if self.use_saved_prompts:
            before = len(all_images)
            # Keep only scenes that received a timestamp from prompts.json (audio_lock_time > 0).
            # Scenes without a lock time are orphaned (no matching prompts.json entry) and have
            # unreliable word-sync timestamps that overlap with real scenes, causing drift.
            all_images = [img for img in all_images
                          if img.audio_lock_time > 0 and not img.is_title_card]
            dropped = before - len(all_images)
            if dropped:
                print(f"   Dropped {dropped} scene(s) without prompts.json timestamps")

        if not any(img.status == "done" for img in all_images):
            print("❌ No images generated!")
            return None

        all_images.sort(key=lambda x: x.start_time)

        # No-compile mode: stop here, images are done
        if self.config.no_compile:
            print(f"\n✅ Images generated. Skipping compile (--no-compile).")
            return True

        # Compile video
        output_path = self.workspace / 'videos' / f'{video_name}.mp4'

        if not os.path.exists(audio_path):
            print(f"⚠ Audio file needed for compilation: {audio_path}")
            return None

        success = self.compiler.compile(
            images=all_images,
            audio_path=audio_path,
            output_path=str(output_path),
            intro_path=intro_path,
            intro_duration=intro_audio_duration
        )
        
        if success:
            print(f"\n✅ VIDEO COMPLETE: {output_path}")
            return str(output_path)
        
        return None

    def process_timeline(self, timeline_file: str, audio_path: str, video_name: str) -> Optional[str]:
        """
        Process strictly following a precomputed timeline JSON.
        No timing modification allowed.
        """
        print("\n" + "="*60)
        print("🎬 VIDEO AUTOMATION - Strict Timeline Renderer")
        print(f"   Model: {self.config.ai33_model}")
        print("="*60)
        
        # 1. Validate JSON programmatically
        if not os.path.exists(timeline_file):
            print(f"❌ Timeline file not found: {timeline_file}")
            return None
            
        with open(timeline_file, 'r', encoding='utf-8') as f:
            timeline_data = json.load(f)
            
        print("\n🔎 Validating timeline JSON...")
        for i, scene in enumerate(timeline_data):
            # Enforce required keys
            for key in ['type', 'start', 'end', 'duration']:
                if key not in scene:
                    raise ValueError(f"CRITICAL ERROR: Scene {i} missing '{key}'. JSON validation failed.")
            
            # Duration check (abort if <= 0)
            if scene['duration'] <= 0:
                raise ValueError(f"CRITICAL ERROR: Scene {i} duration ({scene['duration']}s) is invalid! Cannot be <= 0.")
            
            print(f"   ✓ Valid: Scene {i} | Type: {scene['type']} | {scene['start']}s -> {scene['end']}s | Dur: {scene['duration']}s")
            
        # Assets & dirs
        images_dir = self.workspace / 'images' / video_name
        images_dir.mkdir(parents=True, exist_ok=True)
        intro_path = self._find_intro()
        
        # Prepare list for SceneImages
        rendered_images = []
        intro_duration = 0.0
        
        # 2. Render Loop
        for i, scene_data in enumerate(timeline_data):
            stype = scene_data['type']
            dur = scene_data['duration']
            
            if stype == "intro":
                intro_duration = dur
                continue
                
            scene = SceneImage(
                index=i,
                segment_number=scene_data.get('number', 0),
                segment_title="",
                text=scene_data.get('prompt', ''),
                start_time=scene_data['start'],
                end_time=scene_data['end'],
                duration=dur,
                include_character=False
            )
            
            if stype == "number_card":
                # Render full white background, large black text, no overlays
                out_path = str(images_dir / f"number_card_{scene.segment_number:02d}.png")
                self._generate_number_card(str(scene.segment_number), out_path)
                scene.image_path = out_path
                scene.status = "done"
                rendered_images.append(scene)
                print(f"   ✓ Rendered Number Card: #{scene.segment_number}")
                
            elif stype == "content":
                # AI generation based on timeline prompt
                out_path = str(images_dir / f"scene_{i:04d}.png")
                
                print(f"   🎨 Generating Scene {i} ({dur}s): {scene.text[:50]}...")
                
                success, error, credits = self.generator.generate(
                    prompt=scene.text,
                    output_path=out_path
                )
                
                if success:
                    # Enforce resolution & scaling (1920x1080)
                    with Image.open(out_path) as img:
                        w, h = img.size
                        if w < 1280 or h < 720:
                            print(f"   ⚠ WARNING: Image {i} resolution {w}x{h} too small. Generating again!")
                            
                    scene.image_path = out_path
                    scene.status = "done"
                    rendered_images.append(scene)
                    print(f"      ✓ Success ({credits} cr)")
                else:
                    print(f"      ❌ Failed: {error}")
                    scene.status = "failed"
                    return None
                    
        # Sort by start_time
        rendered_images.sort(key=lambda x: x.start_time)

        # 3. Final Verification (Validation Logging)
        print("\n📊 TIMELINE RENDERING VALIDATION LOG")
        for idx, img in enumerate(rendered_images):
            # Abort if any mismatch in duration
            json_scene = next((s for s in timeline_data if s['start'] == img.start_time and s.get('type') != 'intro'), None)
            if json_scene and abs(json_scene['duration'] - img.duration) > 0.01:
                raise ValueError(f"CRITICAL ERROR: Duration mismatch on scene {idx}! JSON: {json_scene['duration']} vs Render: {img.duration}")
            
            print(f"   Scene {idx}: [{img.start_time:.2f}s -> {img.end_time:.2f}s] | Dur: {img.duration:.2f}s | Type: {'Number' if 'number_card' in img.image_path else 'Content'}")
            
        # Compile video
        output_path = self.workspace / 'videos' / f'{video_name}.mp4'
        if not os.path.exists(audio_path):
            print(f"❌ Audio file needed for compilation: {audio_path}")
            return None
            
        print("\n🎬 Compiling video strictly honoring timeline...")
        success = self.compiler.compile(
            images=rendered_images,
            audio_path=audio_path,
            output_path=str(output_path),
            intro_path=intro_path,
            intro_duration=intro_duration
        )
        
        if success:
            print(f"\n✅ STRICT TIMELINE VIDEO COMPLETE: {output_path}")
            return str(output_path)
            
        return None

    def _generate_number_card(self, number_str: str, output_path: str):
        """
        Rule: Render full white background. Center large black text showing only the number.
        No overlay. No borders. No animation. Hard cut.
        """
        w, h = self.config.image_width, self.config.image_height
        img = Image.new('RGB', (w, h), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        
        try:
            font_size = 400
            try:
                font = ImageFont.truetype("arialbd.ttf", font_size)
            except IOError:
                try:
                    font = ImageFont.truetype("LiberationSans-Bold.ttf", font_size)
                except IOError:
                    font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
            
        text = str(number_str)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        
        x = (w - text_w) / 2
        y = (h - text_h) / 2
        
        draw.text((x, y), text, fill=(0, 0, 0), font=font)
        img.save(output_path)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Video Automation V2 - Segment Based")
    parser.add_argument('--audio', '-a', required=True, help='Audio file path')
    parser.add_argument('--name', '-n', required=True, help='Video name')
    parser.add_argument('--ai33-key', '-k', required=True, help='AI33 API key')
    parser.add_argument('--title', '-t', default='', help='Video title')
    parser.add_argument('--transcript', '-j', default='', help='JSON transcript file (skip transcription)')
    parser.add_argument('--workspace', '-d', default='video_workspace', help='Workspace dir')
    parser.add_argument('--workers', '-w', type=int, default=10, help='Parallel workers for image generation (default: 10)')
    parser.add_argument('--compile-workers', type=int, default=3, help='Parallel FFmpeg workers for Ken Burns/clip pass (default: 3, lower = less RAM)')
    parser.add_argument('--character-rate', '-c', type=float, default=0.20, help='Character rate (default: 0.20)')
    parser.add_argument('--model', '-m', default='', help='AI33 model ID (leave empty to select interactively)')
    parser.add_argument('--select-model', '-s', action='store_true', help='Show model list and select interactively')
    parser.add_argument('--list-models', '-l', action='store_true', help='Just list models and exit')
    
    # Local LLM options
    parser.add_argument('--use-llm', action='store_true', help='Use LLM for better prompts (Ollama/LM Studio/Claude)')
    parser.add_argument('--use-saved-prompts', action='store_true', help='Load prompts from scripts/{name}_prompts.json instead of generating them')
    parser.add_argument('--scene-offset', type=float, default=2.5, help='Seconds to delay each image within its scene window for audio sync (default: 2.5)')
    parser.add_argument('--llm-provider', default='ollama', choices=['ollama', 'lmstudio', 'claude'], help='LLM provider: ollama, lmstudio, or claude (uses Claude Code CLI)')
    parser.add_argument('--llm-model', default='llama3.2', help='Local LLM model name (default: llama3.2, not needed for claude provider)')
    parser.add_argument('--llm-url', default='', help='Custom LLM API URL (optional, not needed for claude provider)')
    parser.add_argument('--anthropic-key', default='', help='Anthropic API key for Claude batch prompt generation (context-aware, all scenes in one call)')
    parser.add_argument('--claude-model', default='claude-sonnet-4-6',
                        choices=['claude-sonnet-4-6', 'claude-opus-4-6', 'claude-haiku-4-5-20251001'],
                        help='Claude model for prompt generation (default: claude-sonnet-4-6)')
    
    parser.add_argument('--compile-only', action='store_true', help='Skip image generation; recompile video from existing images only')
    parser.add_argument('--prompts-only', action='store_true', help='Stop after generating and saving prompts.json; skip image generation and video compile')
    parser.add_argument('--scene-text-only', action='store_true', help='Export scene texts + Claude batch prompt, then stop (no LLM, no images)')
    parser.add_argument('--no-compile', action='store_true', help='Generate images but skip the final video compile step')
    parser.add_argument('--timeline-json', help='Strict JSON timeline to render (skip transcribe and Qwen planning)')

    # Effects
    parser.add_argument('--ken-burns', action='store_true', help='Apply slow zoom/pan (Ken Burns) effect to each image during compile')
    parser.add_argument('--crossfade', action='store_true', help='Apply crossfade dissolve between scenes during compile')
    parser.add_argument('--crossfade-duration', type=float, default=0.4, help='Crossfade duration in seconds (default: 0.4)')

    # Regen
    parser.add_argument('--regen-scenes', default='', help='Comma-separated 1-based scene numbers to force re-generate, e.g. "5,12,18"')
    parser.add_argument('--find-dupes', action='store_true', help='After generation, scan for visually similar/duplicate images and auto-regenerate them')
    parser.add_argument('--dupe-threshold', type=int, default=10, help='Hamming distance threshold for duplicate detection (default: 10, lower = stricter)')
    
    args = parser.parse_args()
    
    # If --list-models, just show models and exit
    if args.list_models:
        selector = AI33ModelSelector(args.ai33_key)
        selector.display_and_select()
        sys.exit(0)
    
    config = Config()
    config.max_workers = args.workers
    config.compile_workers = args.compile_workers
    config.character_rate = args.character_rate
    config.scene_display_offset = args.scene_offset
    config.compile_only = args.compile_only
    config.prompts_only = args.prompts_only
    config.scene_text_only = args.scene_text_only
    config.no_compile = args.no_compile
    config.find_dupes = args.find_dupes
    config.dupe_threshold = args.dupe_threshold
    config.ken_burns = args.ken_burns
    config.crossfade = args.crossfade
    config.crossfade_duration = args.crossfade_duration
    if args.regen_scenes.strip():
        config.regen_scenes = [int(x.strip()) for x in args.regen_scenes.split(',') if x.strip().isdigit()]
    
    # Model selection
    if args.select_model:
        selector = AI33ModelSelector(args.ai33_key)
        selected = selector.display_and_select()
        if selected:
            config.ai33_model = selected
        elif args.model:
            config.ai33_model = args.model
    elif args.model:
        config.ai33_model = args.model
    
    print(f"\nImage model selection restored. No default override active.")
    print(f"📷 Using model: {config.ai33_model}")
    
    automation = VideoAutomation(
        api_key=args.ai33_key,
        workspace=args.workspace,
        config=config,
        use_local_llm=args.use_llm,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        use_saved_prompts=args.use_saved_prompts,
        anthropic_api_key=args.anthropic_key or None,
        claude_model=args.claude_model
    )
    
    if args.timeline_json:
        result = automation.process_timeline(
            timeline_file=args.timeline_json,
            audio_path=args.audio,
            video_name=args.name
        )
    else:
        # Handle "0" as None so the system knows to transcribe
        t_file = None
        if args.transcript and str(args.transcript).strip() != "0" and str(args.transcript).lower() != "false":
            t_file = args.transcript
            
        result = automation.process(
            audio_path=args.audio,
            video_name=args.name,
            video_title=args.title,
            transcript_file=t_file
        )
    
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
