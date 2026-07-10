"""
Global parameters for the GIM therapy application.
This file contains all configurable parameters used throughout the application.
"""
from __future__ import annotations


# Global constants
NUM_MUSIC_TRACKS = 2  # Number of music tracks to select and display
USE_KIMUSIC_GENERATION = True  # Phase 1 proxy metadata only; keep default behavior unchanged
ADMIN_PASSWORD = "change-me"

# Session parameters
MAX_MEMORY_ITEMS = 20
MAX_CONVERSATION_LENGTH = 10
SUMMARIZATION_THRESHOLD = 10  # Trigger summarization when exceeding this number of messages

# API parameters
MODEL_NAME =  "gpt-5.5" #claude-3-5-sonnet-latest"
MUSIC_MODEL_NAME= "gpt-4o-audio-preview"
MAX_TOKENS_RESPONSE = 1000
MAX_TOKENS_SUMMARY = 500


#music tags
MOOD_OPTIONS_NUM=30
GENRE_OPTIONS_NUM=30

# Elasticsearch parameters
REBUILD_MUSIC_INDEX = True

CANDIDATES_PER_PHASE = 1

