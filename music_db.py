from __future__ import annotations
import json
import os
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.metrics.pairwise import cosine_similarity
import time
import http.client
import re
from collections import Counter
import logging
from music_document import MusicDocumentManager
from hyper_parameters import MODEL_NAME, MAX_TOKENS_RESPONSE

# 閰嶇疆鏃ュ織
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MusicDatabase:
    def __init__(self, json_file_path, use_elasticsearch=True, es_host="http://localhost:9200", es_index="music_therapy", rebuild_index=False):
        """
        Initialize the music database with the given JSON file path.
        
        Args:
            json_file_path: 闊充箰JSON鏁版嵁鏂囦欢璺緞
            use_elasticsearch: 鏄惁浣跨敤Elasticsearch杩涜鎼滅储锛堝鏋滃彲鐢級
            es_host: Elasticsearch涓绘満鍦板潃
            es_index: Elasticsearch绱㈠紩鍚嶇О
        """
        self.json_file_path = json_file_path
        self.music_data = []
        self.feature_vectors = None
        self.scaler = StandardScaler()
        
        # 娣诲姞ES鏀寔
        self.use_elasticsearch = use_elasticsearch
        self.es_host = es_host
        self.es_index = es_index
        # self.es_client = None
        self.MusicDocumentManager = None
        
        # Collections for all unique music attributes with frequency counting
        # Counters for raw attribute frequencies
        self.all_tags = Counter()
        self.all_genres = Counter()
        self.all_moods = Counter()
        self.all_movements = Counter()
        self.all_themes = Counter()
        
        # Cached attribute options (initialized in extract_unique_attributes)
        self._cached_mood_options = []
        self._cached_genre_options = []
        self._cached_tag_options = []
        self._cached_theme_options = []
        self._cached_movement_options = []
        self._last_cache_update = None
        
        # Collections for tempo and dynamics distribution
        self.tempo_distribution = {
            "slow": 0,   # < 80 BPM
            "medium": 0, # 80-120 BPM
            "fast": 0    # > 120 BPM
        }
        self.dynamics_distribution = {
            "soft": 0,     # < 0.1
            "moderate": 0, # 0.1-0.2
            "intense": 0   # > 0.2
        }
        
        # Raw numerical distributions
        self.tempo_values = []
        self.dynamics_values = []
        
        # 鍔犺浇鏁版嵁
        self.load_music_data()
        self.extract_unique_attributes()
        
        # 鍒濆鍖朎S
        if use_elasticsearch:
            self.init_elasticsearch(rebuild_index)
            # 妫€鏌ユ槸鍚﹂渶瑕侀噸寤虹储寮曟垨绱㈠紩鏄惁涓虹┖
            if self.use_elasticsearch:
                if rebuild_index or self.is_elasticsearch_empty():
                    if rebuild_index:
                        logger.info("閲嶅缓绱㈠紩妯″紡锛屽紑濮嬪鍏ラ煶涔愭暟鎹?..")
                    else:
                        logger.info("Elasticsearch绱㈠紩涓虹┖锛屽紑濮嬪鍏ラ煶涔愭暟鎹?..")
                    self.import_music_to_elasticsearch()
                else:
                    logger.info("Elasticsearch绱㈠紩宸叉湁鏁版嵁锛岃烦杩囧鍏?)

        self.conn = http.client.HTTPSConnection("api.openai.com")
    
    def init_elasticsearch(self, rebuild_index=False):
        """鍒濆鍖朎lasticsearch瀹㈡埛绔?""
        try:
            # 灏濊瘯浣跨敤Music Document绠＄悊鍣?            self.MusicDocumentManager = MusicDocumentManager(es_host=self.es_host, rebuild_index=rebuild_index)
            logger.info("Music Document绠＄悊鍣ㄥ垵濮嬪寲鎴愬姛")
            self.use_elasticsearch = True
        except Exception as e:
            logger.warning(f"鍒濆鍖朚usic Document绠＄悊鍣ㄥけ璐? {str(e)}")
            logger.warning("灏嗕娇鐢ㄦ湰鍦伴煶涔愭暟鎹绱?)
            self.use_elasticsearch = False
    
    def import_music_to_elasticsearch(self):
        """灏嗛煶涔愭暟鎹鍏ュ埌Elasticsearch"""
        if not self.use_elasticsearch or not self.MusicDocumentManager:
            logger.warning("Elasticsearch涓嶅彲鐢紝璺宠繃鏁版嵁瀵煎叆")
            return False
        
        try:
            logger.info("寮€濮嬪皢闊充箰鏁版嵁瀵煎叆鍒癊lasticsearch...")
            success_count = 0
            failed_count = 0
            
            for i, music_item in enumerate(self.music_data):
                try:
                    # 鍒涘缓Music鏂囨。瀵硅薄
                    music_doc = self.MusicDocumentManager.create_music_document(music_item)
                    
                    if music_doc:
                        # 淇濆瓨鍒癊lasticsearch
                        if self.MusicDocumentManager.save_music_document(music_doc):
                            success_count += 1
                            if success_count % 10 == 0:  # 姣?0鏉¤褰曚竴娆¤繘搴?                                logger.info(f"宸叉垚鍔熷鍏?{success_count} 鏉¤褰?)
                        else:
                            failed_count += 1
                            logger.error(f"淇濆瓨闊充箰鏂囨。澶辫触: {music_item.get('title', 'Unknown')}")
                    else:
                        failed_count += 1
                        logger.error(f"鍒涘缓闊充箰鏂囨。澶辫触: {music_item.get('title', 'Unknown')}")
                        
                except Exception as e:
                    failed_count += 1
                    logger.error(f"澶勭悊闊充箰鏁版嵁鏃跺嚭閿? {str(e)}")
            
            logger.info(f"Elasticsearch鏁版嵁瀵煎叆瀹屾垚 - 鎴愬姛: {success_count}, 澶辫触: {failed_count}")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"瀵煎叆闊充箰鏁版嵁鍒癊lasticsearch鏃跺嚭閿? {str(e)}")
            return False
    
    def is_elasticsearch_empty(self):
        """妫€鏌lasticsearch绱㈠紩鏄惁涓虹┖"""
        if not self.use_elasticsearch or not self.MusicDocumentManager:
            return True
        
        try:
            # 鑾峰彇绱㈠紩涓殑鏂囨。鏁伴噺
            all_music = self.MusicDocumentManager.get_all_music()
            is_empty = len(all_music) == 0
            logger.info(f"Elasticsearch绱㈠紩涓湁 {len(all_music)} 鏉¤褰?)
            return is_empty
        except Exception as e:
            logger.error(f"妫€鏌lasticsearch绱㈠紩鐘舵€佹椂鍑洪敊: {str(e)}")
            return True
    
    def load_music_data(self):
        """Load music data from JSON file."""
        try:
            with open(self.json_file_path, 'r', encoding='utf-8') as f:
                self.music_data = json.load(f)
            print(f"Successfully loaded {len(self.music_data)} music tracks.")
        except Exception as e:
            print(f"Error loading music data: {str(e)}")
            self.music_data = []
    
    def extract_unique_attributes(self):
        """Extract all unique tags, genres, moods, movements, and themes from the dataset with frequency counting."""
        for track in self.music_data:
            # Extract tags
            if "tags" in track and isinstance(track["tags"], list):
                for tag in track["tags"]:
                    self.all_tags[tag.lower()] += 1
            
            # Extract genres
            if "genre" in track and isinstance(track["genre"], list):
                for genre in track["genre"]:
                    self.all_genres[genre.lower()] += 1
            
            # Extract moods
            if "mood" in track and isinstance(track["mood"], list):
                for mood in track["mood"]:
                    self.all_moods[mood.lower()] += 1
            
            # Extract movements
            if "movement" in track and isinstance(track["movement"], list):
                for movement in track["movement"]:
                    self.all_movements[movement.lower()] += 1
            
            # Extract themes
            if "theme" in track and isinstance(track["theme"], list):
                for theme in track["theme"]:
                    self.all_themes[theme.lower()] += 1
            
            # Extract tempo and dynamics
            if "audio_features" in track:
                features = track["audio_features"]
                
                # Extract tempo
                if "tempo" in features and features["tempo"] is not None:
                    tempo = features["tempo"]
                    self.tempo_values.append(tempo)
                    
                    # Categorize tempo
                    if tempo < 80:
                        self.tempo_distribution["slow"] += 1
                    elif tempo <= 120:
                        self.tempo_distribution["medium"] += 1
                    else:
                        self.tempo_distribution["fast"] += 1
                
                # Extract dynamics (using rmse mean as a proxy)
                if "dynamics_rmse_mean" in features and features["dynamics_rmse_mean"] is not None:
                    dynamics = features["dynamics_rmse_mean"]
                    self.dynamics_values.append(dynamics)
                    
                    # Categorize dynamics
                    if dynamics < 0.1:
                        self.dynamics_distribution["soft"] += 1
                    elif dynamics <= 0.2:
                        self.dynamics_distribution["moderate"] += 1
                    else:
                        self.dynamics_distribution["intense"] += 1
        
        # Update cached options
        self._cached_tag_options = [tag for tag, _ in self.all_tags.most_common(50)]
        self._cached_genre_options = [genre for genre, _ in self.all_genres.most_common(30)]
        self._cached_mood_options = [mood for mood, _ in self.all_moods.most_common(30)]
        self._cached_movement_options = [movement for movement, _ in self.all_movements.most_common(30)]
        self._cached_theme_options = [theme for theme, _ in self.all_themes.most_common(30)]
        self._last_cache_update = time.time()
        
        # Print attribute statistics
        print(f"Extracted {len(self.all_tags)} unique tags, {len(self.all_genres)} genres, "
              f"{len(self.all_moods)} moods, {len(self.all_movements)} movements, and {len(self.all_themes)} themes.")
        
        # Print the most common attributes for debugging
        print("\nMost common moods:")
        for mood, count in self.all_moods.most_common(5):
            print(f"  {mood}: {count}")
            
        print("\nMost common genres:")
        for genre, count in self.all_genres.most_common(5):
            print(f"  {genre}: {count}")
        
        # Print tempo and dynamics distribution
        total_tempo_tracks = sum(self.tempo_distribution.values())
        if total_tempo_tracks > 0:
            print("\nTempo distribution:")
            for tempo_category, count in self.tempo_distribution.items():
                percentage = (count / total_tempo_tracks) * 100
                print(f"  {tempo_category}: {count} tracks ({percentage:.1f}%)")
        
        total_dynamics_tracks = sum(self.dynamics_distribution.values())
        if total_dynamics_tracks > 0:
            print("\nDynamics distribution:")
            for dynamics_category, count in self.dynamics_distribution.items():
                percentage = (count / total_dynamics_tracks) * 100
                print(f"  {dynamics_category}: {count} tracks ({percentage:.1f}%)")
        
        # Calculate and print average tempo and dynamics
        if self.tempo_values:
            avg_tempo = sum(self.tempo_values) / len(self.tempo_values)
            print(f"\nAverage tempo: {avg_tempo:.1f} BPM")
            
        if self.dynamics_values:
            avg_dynamics = sum(self.dynamics_values) / len(self.dynamics_values)
            print(f"Average dynamics (RMSE): {avg_dynamics:.3f}")
    
    def get_attribute_options(self, attribute_type, max_items=30):
        """Get a list of the most common attributes of the specified type for prompting.
        
        Args:
            attribute_type: The type of attribute to get options for ('mood', 'genre', 'tag', 'theme', 'movement')
            max_items: Maximum number of items to return
            
        Returns:
            List of attribute options
        """
        # First try to get from cache
        cache_map = {
            'mood': self._cached_mood_options,
            'genre': self._cached_genre_options,
            'tags': self._cached_tag_options,
            'theme': self._cached_theme_options,
            'movement': self._cached_movement_options
        }
        
        # If we have valid cached options, use them
        if attribute_type in cache_map and cache_map[attribute_type]:
            return cache_map[attribute_type][:max_items]
            
        # If cache is empty, try using Music Document
        if self.use_elasticsearch and self.MusicDocumentManager:
            try:
                logger.info(f"浣跨敤Music Document鑾峰彇{attribute_type}灞炴€ч€夐」")
                options = self.MusicDocumentManager.get_attribute_options(attribute_type, max_items)
                if options:
                    logger.info(f"Music Document杩斿洖浜唟len(options)}涓獅attribute_type}閫夐」")
                    # Update cache with new values
                    if attribute_type in cache_map:
                        cache_map[attribute_type] = options
                        self._last_cache_update = time.time()
                    return options
            except Exception as e:
                logger.error(f"Music Document鑾峰彇灞炴€ч€夐」澶辫触: {str(e)}")
                logger.warning("鍥為€€鍒版湰鍦板睘鎬ч€夐」")
        
        # If both cache and Music Document fail, fall back to local counters
        counter_map = {
            'mood': self.all_moods,
            'genre': self.all_genres,
            'tags': self.all_tags,
            'theme': self.all_themes,
            'movement': self.all_movements
        }
        
        if attribute_type in counter_map:
            options = [item for item, _ in counter_map[attribute_type].most_common(max_items)]
            # Update cache
            if attribute_type in cache_map:
                cache_map[attribute_type] = options
                self._last_cache_update = time.time()
            return options
            
        return []  # Return empty list for unknown attribute types
    
    def refresh_attribute_cache(self):
        """Manually refresh the cached attribute options.
        
        This can be called if the underlying data has changed and you need to update
        the cached options without restarting the application.
        """
        logger.info("Refreshing attribute cache...")
        
        # Clear existing cache
        self._cached_tag_options = []
        self._cached_genre_options = []
        self._cached_mood_options = []
        self._cached_theme_options = []
        self._cached_movement_options = []
        
        # Clear counters
        self.all_tags.clear()
        self.all_genres.clear()
        self.all_moods.clear()
        self.all_themes.clear()
        self.all_movements.clear()
        
        # Re-extract attributes
        self.extract_unique_attributes()
        logger.info("Attribute cache refresh complete")

    # def prepare_feature_vectors(self):
    #     """Prepare feature vectors for music similarity calculation."""
    #     # Extract features from each track
    #     feature_vectors = []
    #     valid_indices = []
        
    #     for i, track in enumerate(self.music_data):
    #         features = track.get("audio_features", {})
            
    #         # Skip tracks with errors or missing features
    #         if features.get("error") is not None:
    #             continue
                
    #         # Extract numerical features
    #         track_features = []
            
    #         # Add tempo
    #         if "tempo" in features:
    #             track_features.append(features["tempo"])
    #         else:
    #             track_features.append(0.0)
                
    #         # Add dynamics
    #         if "dynamics_rmse_mean" in features and "dynamics_rmse_std" in features:
    #             track_features.append(features["dynamics_rmse_mean"])
    #             track_features.append(features["dynamics_rmse_std"])
    #         else:
    #             track_features.extend([0.0, 0.0])
                
    #         # Add timbre (first 5 MFCCs)
    #         if "timbre_mfcc_mean" in features:
    #             mfccs = features["timbre_mfcc_mean"]
    #             track_features.extend(mfccs[:5] if len(mfccs) >= 5 else mfccs + [0.0] * (5 - len(mfccs)))
    #         else:
    #             track_features.extend([0.0] * 5)
                
    #         # Add pitch/chroma (first 5 values)
    #         if "pitch_chroma_mean" in features:
    #             chroma = features["pitch_chroma_mean"]
    #             track_features.extend(chroma[:5] if len(chroma) >= 5 else chroma + [0.0] * (5 - len(chroma)))
    #         else:
    #             track_features.extend([0.0] * 5)
            
    #         feature_vectors.append(track_features)
    #         valid_indices.append(i)
        
    #     # Scale features to normalize them
    #     if feature_vectors:
    #         feature_vectors = self.scaler.fit_transform(feature_vectors)
            
    #     self.feature_vectors = feature_vectors
    #     self.valid_indices = valid_indices
    #     print(f"Prepared feature vectors for {len(feature_vectors)} tracks.")
    
    # def generate_music_query(self, therapy_state, user_focus, user_mood, user_preferences=None):
    #     """
    #     Generate a music query based on therapy state and user information.
    #     This will be used to prompt the LLM to specify music retrieval criteria.
    #     """
    #     # Get mood and genre options to include in the prompt
    #     mood_options = self.get_attribute_options("mood", 20)
    #     genre_options = self.get_attribute_options("genre", 15)
        
    #     mood_options_str = ", ".join(mood_options)
    #     genre_options_str = ", ".join(genre_options)
        
    #     template = (
    #         f"Based on the user's current therapy state: '{therapy_state}', "
    #         f"with a focus intention of '{user_focus}', and current mood: '{user_mood}', "
    #         "I need to select appropriate music for guided imagery."
    #         "\n\nPlease specify the ideal musical characteristics for therapeutic support:"
    #         "\n- Tempo (slow/medium/fast): "
    #         "\n- Dynamics (soft/moderate/intense): "
    #         f"\n- Mood (select from these available options: {mood_options_str}): "
    #         f"\n- Musical style/genre (select from these available options: {genre_options_str}): "
    #         "\n- Any specific elements to include or avoid: "
    #     )
        
    #     if user_preferences:
    #         template += f"\n\nNote that the user has expressed preferences for: {user_preferences}"
            
    #     return template
    
    # def parse_llm_music_criteria(self, llm_response):
    #     """
    #     Parse the LLM's response to extract music selection criteria.
    #     Returns a dictionary of criteria for music selection.
    #     """
    #     criteria = {
    #         "tempo_preference": None,  # slow, medium, fast
    #         "dynamics_preference": None,  # soft, moderate, intense
    #         "mood_keywords": [],
    #         "genre_keywords": [],
    #         "avoid_keywords": []
    #     }
        
    #     # Extract tempo preference
    #     if "slow" in llm_response.lower():
    #         criteria["tempo_preference"] = "slow"
    #     elif "fast" in llm_response.lower():
    #         criteria["tempo_preference"] = "fast"
    #     elif "medium" in llm_response.lower():
    #         criteria["tempo_preference"] = "medium"
            
    #     # Extract dynamics preference
    #     if "soft" in llm_response.lower():
    #         criteria["dynamics_preference"] = "soft"
    #     elif "intense" in llm_response.lower():
    #         criteria["dynamics_preference"] = "intense"
    #     elif "moderate" in llm_response.lower():
    #         criteria["dynamics_preference"] = "moderate"
            
    #     # Extract mood keywords (this is simplified - would need enhancement for production)
    #     mood_keywords = ["calming", "peaceful", "energizing", "reflective", "uplifting", 
    #                      "melancholic", "joyful", "serene", "powerful", "gentle", 
    #                      "dramatic", "ethereal", "inspirational", "meditative", "nostalgic"]
        
    #     for keyword in mood_keywords:
    #         if keyword in llm_response.lower():
    #             criteria["mood_keywords"].append(keyword)
                
    #     # Extract genre preferences
    #     genre_keywords = ["classical", "ambient", "jazz", "piano", "orchestral", 
    #                       "electronic", "instrumental", "folk", "world", "nature sounds"]
        
    #     for keyword in genre_keywords:
    #         if keyword in llm_response.lower():
    #             criteria["genre_keywords"].append(keyword)
                
    #     # Extract things to avoid
    #     avoid_indicators = ["avoid", "exclude", "not include", "stay away from"]
    #     for indicator in avoid_indicators:
    #         if indicator in llm_response.lower():
    #             # Find the sentence containing this indicator
    #             sentences = llm_response.split('.')
    #             for sentence in sentences:
    #                 if indicator in sentence.lower():
    #                     # Extract what comes after the indicator
    #                     parts = sentence.lower().split(indicator)
    #                     if len(parts) > 1:
    #                         avoid_text = parts[1].strip()
    #                         avoid_keywords = [k.strip() for k in avoid_text.split(',')]
    #                         criteria["avoid_keywords"].extend(avoid_keywords)
        
    #     return criteria
    
    def get_music_criteria_json(self, system_prompt, user_prompt, api_key):
        """
        Get music selection criteria directly as JSON from LLM.
        
        Args:
            system_prompt: The system prompt to send to the LLM
            user_prompt: The user prompt to send to the LLM
            api_key: API key for the LLM service
            
        Returns:
            A dictionary with music selection criteria
        """
        # 鐩存帴鐢ㄤ紶鍏ョ殑system_prompt鍜寀ser_prompt
        llm_messages = [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
        
        # Make API request
        try:
            
            payload = json.dumps({
                "model": MODEL_NAME,
                "max_tokens": MAX_TOKENS_RESPONSE,
                "messages": llm_messages
            })
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            
            self.conn.request("POST", "/v1/messages", payload, headers)
            start_time = time.time()
            response = self.conn.getresponse()  ##todo: 浼樺寲music generate prompt鍜宻ystem prompt鏈夌偣閲嶅  
            #鍔犲揩鐢熸垚鏃堕棿锛屼篃璁稿彲浠ュ湪涓婁竴涓樁娈靛氨鍚屾鐨勮繘琛岄煶涔愮殑椋庢牸鐢熸垚
            end_time = time.time()
            response_data = json.loads(response.read().decode("utf-8"))
            print(f"Time taken for llm generate: {end_time - start_time} seconds")
            
            # Extract response text
            if isinstance(response_data, dict):
                if 'choices' in response_data:
                    extracted_text = response_data['choices'][0]['message']['content']
                elif 'content' in response_data:
                    extracted_text = response_data['content'][0]['text']
                else:
                    print("RAW RESPONSE:")
                    print(response_data)
                    print("Unexpected API response format")
                    return self._get_default_criteria()
                
                    
                # Try to parse JSON from the response
                try:
                    # Extract JSON if it's embedded in markdown code blocks
                    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', extracted_text)
                    if json_match:
                        extracted_text = json_match.group(1)

                    print("!!!!!Music criteria JSON:")
                    print(extracted_text)

                    criteria = json.loads(extracted_text)
                    
                    # Validate and ensure we have the expected format
                    return self._validate_music_criteria(criteria)
                    
                except json.JSONDecodeError:
                    print("Failed to parse JSON from LLM response")
                    return self._get_default_criteria()
            else:
                print("Unexpected API response type")
                return self._get_default_criteria()
                
        except Exception as e:
            print(f"Error calling LLM API for music criteria: {str(e)}")
            return self._get_default_criteria()
    
    def _get_default_criteria(self):
        """Return default music criteria if LLM fails"""
        return {
            "tempo_preference": "medium",
            "dynamics_preference": "moderate",
            "mood_keywords": ["calming", "reflective"],
            "genre_keywords": ["classical", "ambient"],
            "avoid_keywords": []
        }
        
    def _validate_music_criteria(self, criteria):
        """Validate and fix music criteria if needed"""
        start_time = time.time()
        valid_criteria = {
            "tempo_preference": None,
            "dynamics_preference": None,
            "mood_keywords": [],
            "genre_keywords": [],
            "avoid_keywords": []
        }
        
        # Validate tempo preference
        if "tempo_preference" in criteria:
            if criteria["tempo_preference"] in ["slow", "medium", "fast"]:
                valid_criteria["tempo_preference"] = criteria["tempo_preference"]
            else:
                valid_criteria["tempo_preference"] = "medium"
        else:
            valid_criteria["tempo_preference"] = "medium"
            
        # Validate dynamics preference
        if "dynamics_preference" in criteria:
            if criteria["dynamics_preference"] in ["soft", "moderate", "intense"]:
                valid_criteria["dynamics_preference"] = criteria["dynamics_preference"]
            else:
                valid_criteria["dynamics_preference"] = "moderate"
        else:
            valid_criteria["dynamics_preference"] = "moderate"
            
        # Validate mood keywords
        if "mood_keywords" in criteria and isinstance(criteria["mood_keywords"], list):
            valid_criteria["mood_keywords"] = criteria["mood_keywords"]
        else:
            valid_criteria["mood_keywords"] = ["calming", "reflective"]
            
        # Validate genre keywords
        if "genre_keywords" in criteria and isinstance(criteria["genre_keywords"], list):
            valid_criteria["genre_keywords"] = criteria["genre_keywords"]
        else:
            valid_criteria["genre_keywords"] = ["classical", "ambient"]
            
        # Validate avoid keywords
        if "avoid_keywords" in criteria and isinstance(criteria["avoid_keywords"], list):
            valid_criteria["avoid_keywords"] = criteria["avoid_keywords"]
        
        end_time = time.time()
        print(f"Time taken for validate music criteria: {end_time - start_time} seconds")
        
        return valid_criteria
    
    def music_matches_criteria(self, track, criteria):
        """Check if a music track matches the given criteria."""
        score = 0
        max_score = 0
        
        # Check tempo preference
        if criteria["tempo_preference"]:
            max_score += 1
            tempo = track.get("audio_features", {}).get("tempo", 0)
            
            if criteria["tempo_preference"] == "slow" and tempo < 80:
                score += 1
            elif criteria["tempo_preference"] == "medium" and 80 <= tempo <= 120:
                score += 1
            elif criteria["tempo_preference"] == "fast" and tempo > 120:
                score += 1
        
        # Check dynamics preference
        if criteria["dynamics_preference"]:
            max_score += 1
            dynamics = track.get("audio_features", {}).get("dynamics_rmse_mean", 0)
            
            if criteria["dynamics_preference"] == "soft" and dynamics < 0.1:
                score += 1
            elif criteria["dynamics_preference"] == "moderate" and 0.1 <= dynamics <= 0.2:
                score += 1
            elif criteria["dynamics_preference"] == "intense" and dynamics > 0.2:
                score += 1
        
        # Check mood keywords
        if criteria["mood_keywords"]:
            max_score += 1
            track_moods = track.get("mood", []) + track.get("tags", [])
            
            # Check if any of the desired moods match
            if any(any(desired.lower() in actual.lower() for actual in track_moods) 
                   for desired in criteria["mood_keywords"]):
                score += 1
        
        # Check genre keywords
        if criteria["genre_keywords"]:
            max_score += 1
            track_genres = track.get("genre", []) + track.get("tags", [])
            
            # Check if any of the desired genres match
            if any(any(desired.lower() in actual.lower() for actual in track_genres) 
                   for desired in criteria["genre_keywords"]):
                score += 1
        
        # Check avoid keywords
        if criteria["avoid_keywords"]:
            max_score += 1
            track_keywords = (track.get("tags", []) + track.get("mood", []) + 
                             track.get("genre", []) + track.get("theme", []))
            
            # Check if none of the avoided keywords match
            if not any(any(avoided.lower() in actual.lower() for actual in track_keywords) 
                      for avoided in criteria["avoid_keywords"]):
                score += 1
        
        # Return match score (percentage)
        return score / max(max_score, 1) if max_score > 0 else 0.5
    
    def retrieve_music_for_therapy(self, criteria, num_tracks=4):
        """
        浣跨敤Elasticsearch妫€绱㈤煶涔愩€?        浼樺厛浣跨敤Music Document锛屽鏋滀笉鍙敤鍒欏洖閫€鍒板師鏈塃S闆嗘垚鎴栨湰鍦版悳绱€?        
        Args:
            criteria: 闊充箰閫夋嫨鏍囧噯
            num_tracks: 瑕佽繑鍥炵殑鏇茬洰鏁伴噺
            
        Returns:
            鍖归厤鐨勯煶涔愬垪琛?        """
        # 棣栧厛灏濊瘯浣跨敤Music Document绠＄悊鍣?        if self.use_elasticsearch and self.MusicDocumentManager:
            try:
                logger.info("浣跨敤Music Document鎼滅储闊充箰")
                tracks = self.MusicDocumentManager.search_music(criteria, size=num_tracks)
                
                if tracks and len(tracks) > 0:
                    logger.info(f"Music Document杩斿洖浜唟len(tracks)}涓粨鏋?)
                    return tracks
                else:
                    logger.warning("Music Document娌℃湁杩斿洖缁撴灉锛屽皾璇曞叾浠栨柟娉?)
            except Exception as e:
                logger.error(f"Music Document鎼滅储鍑洪敊: {str(e)}")
                logger.warning("灏濊瘯鍏朵粬鎼滅储鏂规硶")
        
        # # 灏濊瘯浣跨敤鍘熸湁鐨凟S闆嗘垚
        # if self.use_elasticsearch and self.es_client:
        #     try:
        #         logger.info("浣跨敤鍘熸湁Elasticsearch闆嗘垚鎼滅储闊充箰")
        #         tracks = self.es_client.search_music(criteria, size=num_tracks)
                
        #         if tracks and len(tracks) > 0:
        #             logger.info(f"鍘熸湁ES闆嗘垚杩斿洖浜唟len(tracks)}涓粨鏋?)
        #             return tracks
        #         else:
        #             logger.warning("鍘熸湁ES闆嗘垚娌℃湁杩斿洖缁撴灉锛屽洖閫€鍒版湰鍦版悳绱?)
        #     except Exception as e:
        #         logger.error(f"鍘熸湁ES闆嗘垚鎼滅储鍑洪敊: {str(e)}")
        #         logger.warning("鍥為€€鍒版湰鍦版悳绱?)
        
        # 鏈湴鎼滅储锛堜綔涓哄鐢級
        #raise NotImplementedError("鏈湴鎼滅储鏈疄鐜?)
        logger.info("浣跨敤鏈湴绠楁硶妫€绱㈤煶涔?)
        track_scores = []
        
        # Score each track based on criteria match
        for i, track in enumerate(self.music_data):
            match_score = self.music_matches_criteria(track, criteria)
            track_scores.append((i, match_score))
        
        # Sort by match score (descending)
        track_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Get top N tracks
        top_tracks = []
        for i, score in track_scores[:num_tracks]:
            track = self.music_data[i].copy()
            track["match_score"] = score
            top_tracks.append(track)
        
        return top_tracks
    
    def format_music_recommendations(self, tracks):
        """Format music recommendations for presentation to the user."""
        result = "Based on your current state and needs, I've selected the following music pieces:\n\n"
        
        for i, track in enumerate(tracks, 1):
            result += f"{i}. \"{track['title']}\"\n"
            result += f"   - Genre: {', '.join(track.get('genre', [])[:2])}\n"
            result += f"   - Mood: {', '.join(track.get('mood', [])[:3])}\n"
            result += f"   - Filename: {track.get('filename', 'Not available')}\n\n"
        
        result += "These selections are designed to support your imagery experience based on your current therapeutic focus."
        return result


# # Example usage:
# if __name__ == "__main__":
#     # Initialize the music database
#     db = MusicDatabase("toy_dataset/music_data_complete.json")
    
#     # Example: Generate a query for the LLM
#     query = db.generate_music_query(
#         therapy_state="music_imaging",
#         user_focus="Finding inner peace and reducing anxiety",
#         user_mood="Slightly anxious but open to exploration"
#     )
#     print("\nGenerated LLM Query:")
#     print(query)
    
#     # Example LLM response (in a real system, this would come from the LLM API)
#     llm_response = """
#     Based on the user's current state in music_imaging phase, with a focus on finding inner peace and reducing anxiety:
    
#     - Tempo: Slow to medium
#     - Dynamics: Soft to moderate
#     - Mood: Calming, peaceful, reflective
#     - Musical style preference: Classical, ambient, or instrumental pieces
#     - Specific elements: Include gentle flowing melodies, avoid sudden dynamic changes or intense passages
#     """
    
#     # Parse LLM response into criteria
#     criteria = db.parse_llm_music_criteria(llm_response)
#     print("\nParsed Music Criteria:")
#     print(criteria)
    
#     # Retrieve matching tracks
#     matching_tracks = db.retrieve_music_for_therapy(criteria, num_tracks=3)
    
#     # Format recommendations
#     recommendations = db.format_music_recommendations(matching_tracks)
#     print("\nMusic Recommendations:")
#     print(recommendations) 
