from __future__ import annotations
import json
import re
import http.client
import threading
from datetime import datetime
from collections import defaultdict
from queue import Queue
from hyper_parameters import MODEL_NAME


def safe_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_text = safe_text(item)
            if item_text:
                parts.append(f"{safe_text(key)}: {item_text}")
        if parts:
            return ", ".join(parts)
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(part for part in (safe_text(item) for item in value) if part)
    return str(value)


def safe_join(values, sep=", "):
    if not values:
        return ""
    return sep.join(part for part in (safe_text(value) for value in values) if part)

class MemoryManager:
    def __init__(self, max_memory_items=10, max_conversation_length=40):
        """Initialize the memory manager for GIM therapy sessions."""
        self.memories = {
            "personal_info": {},       # Personal details about the client
            "focus_intentions": [],     # History of focus intentions
            "emotional_states": [],     # Emotional states observed during the session
            "imagery": [],              # Key imagery described by the client
            "insights": [],             # Insights or realizations from the client
            "preferences": {            # Client preferences
                "music": [],
                "therapy_style": []
            },
            "session_history": [],      # Brief summaries of past sessions
            "conversation_summaries": [] # Summaries of longer conversations
        }
        self.max_memory_items = max_memory_items
        self.max_conversation_length = max_conversation_length
        
        # Thread management
        self.memory_processing_queue = Queue()
        self.summary_processing_queue = Queue()
        self.is_processing_active = True
        self.memory_thread = threading.Thread(target=self._process_memory_queue, daemon=True)
        self.summary_thread = threading.Thread(target=self._process_summary_queue, daemon=True)
        self.memory_thread.start()
        self.summary_thread.start()

    def _process_memory_queue(self):
        """Background thread to process memory extraction tasks"""
        while self.is_processing_active:
            try:
                task = self.memory_processing_queue.get(timeout=1)
                if task:
                    user_message, therapist_message, api_key = task
                    self._process_message_sync(user_message, therapist_message, api_key)
                self.memory_processing_queue.task_done()
            except Exception as e:
                # Queue.get timeout or other error
                pass
    
    def _process_summary_queue(self):
        """Background thread to process conversation summary tasks"""
        while self.is_processing_active:
            try:
                task = self.summary_processing_queue.get(timeout=1)
                if task:
                    chat_history, api_key = task
                    self._summarize_conversation_sync(chat_history, api_key)
                self.summary_processing_queue.task_done()
            except Exception as e:
                # Queue.get timeout or other error
                pass
    
    def process_message(self, user_message, therapist_message=None, api_key=None):
        """
        Process message asynchronously in the background.
        Puts the task in a queue and returns immediately.
        """
        self.memory_processing_queue.put((user_message, therapist_message, api_key))
        return None  # Return immediately, processing happens in background
    
    def _process_message_sync(self, user_message, therapist_message=None, api_key=None):
        """
        Synchronous version of process_message that actually performs the extraction.
        This runs in a background thread.
        """
        # Create a context string including therapist message if available
        context = f"Therapist: {therapist_message}\nClient: {user_message}" if therapist_message else f"Client: {user_message}"
        
        # Create a prompt to ask the LLM to extract information
        extraction_prompt = f"""
        Extract the following information from this GIM therapy session message:
        
        1. Personal information (name, age, occupation, etc.)
        2. Emotional states expressed (happy, sad, anxious, etc.)
        3. Focus intentions or goals mentioned
        4. Imagery described
        5. Insights or realizations expressed
        6. Music preferences mentioned
        
        Message context:
        {context}
        
        Return ONLY a JSON object with these keys: personal_info, emotional_states, focus_intentions, imagery, insights, music_preferences.
        Each key should contain an object or array of relevant information.
        If no information is found for a category, return an empty object or array for that key.
        """
        
        # Create messages for the API call
        llm_messages = [
            {
                "role": "system",
                "content": "You are a experienced guided imagery music therapist that extracts specific information from therapy conversations. Extract the requested information and return it in JSON format only, with no additional text or explanations."
            },
            {
                "role": "user",
                "content": extraction_prompt
            }
        ]
        
        # Make API request
        conn = http.client.HTTPSConnection("api.openai.com")
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": 1000,
            "messages": llm_messages
        })
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        try:
            conn.request("POST", "/v1/chat/completions", payload, headers)
            response = conn.getresponse()
            response_data = json.loads(response.read().decode("utf-8"))
            
            # Extract information from response
            if isinstance(response_data, dict):
                if 'choices' in response_data:
                    extracted_text = response_data['choices'][0]['message']['content']
                elif 'content' in response_data:
                    extracted_text = response_data['content'][0]['text']
                else:
                    print("RAW RESPONSE:")
                    print(response_data)
                    print("Unexpected API response format")
                    return
                    
                # Try to parse JSON from the response
                try:
                    # Extract JSON if it's embedded in markdown code blocks
                    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', extracted_text)
                    if json_match:
                        extracted_text = json_match.group(1)
                    
                    print("!!!!!Extracted text:")
                    print(extracted_text)
                    extracted_info = json.loads(extracted_text)
                    
                    print("!!!!!Extracted info:")
                    print(extracted_info)
                    # Update memories with extracted information
                    self._update_memories_from_extracted_info(extracted_info)
                    
                except json.JSONDecodeError:
                    print("Failed to parse JSON from LLM response")
            else:
                print("Unexpected API response type")
                
        except Exception as e:
            print(f"Error calling LLM API for information extraction: {str(e)}")
    
    def _update_memories_from_extracted_info(self, extracted_info):
        """Update memory structures with information extracted by the LLM"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update personal info
        if 'personal_info' in extracted_info and extracted_info['personal_info']:
            for key, value in extracted_info['personal_info'].items():
                if value and key not in self.memories["personal_info"]:
                    self.memories["personal_info"][key] = value
        
        # Update emotional states
        if 'emotional_states' in extracted_info and extracted_info['emotional_states']:
            for emotion in extracted_info['emotional_states']:
                if isinstance(emotion, dict):
                    # If it's already in the right format
                    if 'emotion' in emotion:
                        self.memories["emotional_states"].append({
                            "emotion": emotion['emotion'],
                            "context": emotion.get('context', ''),
                            "timestamp": timestamp
                        })
                else:
                    # If it's just a string
                    self.memories["emotional_states"].append({
                        "emotion": emotion,
                        "context": "",
                        "timestamp": timestamp
                    })
            
            # Keep only the most recent emotions within the limit
            if len(self.memories["emotional_states"]) > self.max_memory_items:
                self.memories["emotional_states"] = self.memories["emotional_states"][-self.max_memory_items:]
        
        # Update focus intentions
        if 'focus_intentions' in extracted_info and extracted_info['focus_intentions']:
            for intention in extracted_info['focus_intentions']:
                if isinstance(intention, dict):
                    # If it's already in the right format
                    if 'focus' in intention:
                        self.memories["focus_intentions"].append({
                            "focus": intention['focus'],
                            "source": intention.get('source', 'client'),
                            "timestamp": timestamp
                        })
                else:
                    # If it's just a string
                    self.memories["focus_intentions"].append({
                        "focus": intention,
                        "source": "client",
                        "timestamp": timestamp
                    })
            
            # Keep only the most recent focus intentions within the limit
            if len(self.memories["focus_intentions"]) > self.max_memory_items:
                self.memories["focus_intentions"] = self.memories["focus_intentions"][-self.max_memory_items:]
        
        # Update imagery
        if 'imagery' in extracted_info and extracted_info['imagery']:
            for image in extracted_info['imagery']:
                if isinstance(image, dict):
                    # If it's already in the right format
                    if 'description' in image:
                        self.memories["imagery"].append({
                            "description": image['description'],
                            "context": image.get('context', ''),
                            "timestamp": timestamp
                        })
                else:
                    # If it's just a string
                    self.memories["imagery"].append({
                        "description": image,
                        "context": "",
                        "timestamp": timestamp
                    })
            
            # Keep only the most recent imagery within the limit
            if len(self.memories["imagery"]) > self.max_memory_items:
                self.memories["imagery"] = self.memories["imagery"][-self.max_memory_items:]
        
        # Update insights
        if 'insights' in extracted_info and extracted_info['insights']:
            for insight in extracted_info['insights']:
                if isinstance(insight, dict):
                    # If it's already in the right format
                    if 'content' in insight:
                        self.memories["insights"].append({
                            "content": insight['content'],
                            "context": insight.get('context', ''),
                            "timestamp": timestamp
                        })
                else:
                    # If it's just a string
                    self.memories["insights"].append({
                        "content": insight,
                        "context": "",
                        "timestamp": timestamp
                    })
            
            # Keep only the most recent insights within the limit
            if len(self.memories["insights"]) > self.max_memory_items:
                self.memories["insights"] = self.memories["insights"][-self.max_memory_items:]
        
        # Update music preferences
        if 'music_preferences' in extracted_info and extracted_info['music_preferences']:
            for pref in extracted_info['music_preferences']:
                if isinstance(pref, dict):
                    # If it's already in the right format
                    if 'genre' in pref:
                        self.memories["preferences"]["music"].append({
                            "genre": pref['genre'],
                            "sentiment": pref.get('sentiment', 'like'),
                            "timestamp": timestamp
                        })
                else:
                    # If it's just a string
                    parts = pref.lower().split()
                    if 'dislike' in parts or 'hate' in parts or "don't like" in pref.lower():
                        sentiment = "dislike"
                    else:
                        sentiment = "like"
                    
                    genre = pref.replace('like', '').replace('dislike', '').replace('hate', '').strip()
                    
                    self.memories["preferences"]["music"].append({
                        "genre": genre,
                        "sentiment": sentiment,
                        "timestamp": timestamp
                    })
            
            # Keep only the most recent music preferences within the limit
            if len(self.memories["preferences"]["music"]) > self.max_memory_items:
                self.memories["preferences"]["music"] = self.memories["preferences"]["music"][-self.max_memory_items:]
                
    def summarize_session(self, chat_history):
        """Create a summary of the current session for long-term memory."""
        # Simple summary approach: extract key elements from the session
        summary = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "focus_intentions": [item["focus"] for item in self.memories["focus_intentions"][-3:]] if self.memories["focus_intentions"] else [],
            "emotions": list(set([item["emotion"] for item in self.memories["emotional_states"][-5:]])) if self.memories["emotional_states"] else [],
            "key_imagery": [item["description"] for item in self.memories["imagery"][-3:]] if self.memories["imagery"] else [],
            "insights": [item["content"] for item in self.memories["insights"][-3:]] if self.memories["insights"] else []
        }
        
        self.memories["session_history"].append(summary)
        
        # Keep only the most recent session summaries within the limit
        if len(self.memories["session_history"]) > 10:  # We keep fewer session summaries
            self.memories["session_history"] = self.memories["session_history"][-10:]
            
        return summary
    
    def _llm_summarization(self, messages, api_key):
        """
        Use the LLM API to summarize conversation.
        
        Args:
            messages: List of message strings
            api_key: API key for the LLM service
            
        Returns:
            Dict containing summarized information
        """
        # Create a prompt to ask the LLM to summarize the conversation
        summarization_prompt = f"""
        Summarize the following user messages from a GIM therapy session. 
        Extract:
        1. Important user information mentioned
        2. Key concerns or issues expressed
        3. Emotional states mentioned
        4. Main topics discussed
        5. Important imagery described
        
        User messages:
        {" ".join(messages)}
        
        Provide a concise summary in JSON format with these keys: personal_info, concerns, emotions, topics, imagery.
        """
        
        # Create messages for the API call
        llm_messages = [
            {
                "role": "system",
                "content": "You are a skilled guided imagery music therapist that summarizes therapy conversations. Extract key information and return it in JSON format."
            },
            {
                "role": "user",
                "content": summarization_prompt
            }
        ]
        
        # Make API request
        conn = http.client.HTTPSConnection("api.openai.com")
        payload = json.dumps({
            "model": MODEL_NAME,
            "max_tokens": 500,
            "messages": llm_messages
        })
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        try:
            conn.request("POST", "/v1/chat/completions", payload, headers)
            response = conn.getresponse()
            response_data = json.loads(response.read().decode("utf-8"))
            
            # Extract summary from response
            if isinstance(response_data, dict):
                if 'choices' in response_data:
                    summary_text = response_data['choices'][0]['message']['content']
                elif 'content' in response_data:
                    summary_text = response_data['content'][0]['text']
                else:
                    print("Unexpected API response format")
                    print("response_data: ", response_data)
                    return self._basic_summarization(messages)
                    
                # Try to parse JSON from the response
                try:
                    # Extract JSON if it's embedded in markdown code blocks
                    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', summary_text)
                    if json_match:
                        summary_text = json_match.group(1)
                        
                    summary_dict = json.loads(summary_text)
                    return summary_dict
                except json.JSONDecodeError:
                    print("Failed to parse JSON from LLM response, using basic summarization")
                    print("response_data: ", response_data)
                    return self._basic_summarization(messages)
            else:
                print("Unexpected API response type")
                print("response_data: ", response_data)
                return self._basic_summarization(messages)
                
        except Exception as e:
            print(f"Error calling LLM API: {str(e)}")
            return self._basic_summarization(messages)
            
    def summarize_conversation(self, chat_history, api_key=None):
        """
        Asynchronously summarize the conversation when it exceeds max_conversation_length.
        Puts the task in a queue and returns immediately.
        
        Args:
            chat_history: List of message dictionaries with 'role' and 'content'
            api_key: Optional API key for the LLM service
        """
        if len(chat_history) <= self.max_conversation_length:
            return None
            
        # Queue the summarization task to run in background
        self.summary_processing_queue.put((chat_history, api_key))
        
        # Return a placeholder to indicate summarization is in progress
        return {"status": "summarization_in_progress"}
    
    def _summarize_conversation_sync(self, chat_history, api_key=None):
        """
        Synchronous version of summarize_conversation that actually performs the summarization.
        This runs in a background thread.
        """
        if len(chat_history) <= self.max_conversation_length:
            return None
            
        # Extract user messages for summarization
        user_messages = [msg["content"] for msg in chat_history if msg["role"] == "user"]
        
        # Determine which summarization method to use
        if api_key:
            summary = self._llm_summarization(user_messages[-self.max_conversation_length:], api_key)
        else:
            return None  # Don't use basic summarization
        
        # Store the summary
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_obj = {
            "timestamp": timestamp,
            "content": summary,
            "messages_covered": len(user_messages[-self.max_conversation_length:])
        }
        
        self.memories["conversation_summaries"].append(summary_obj)
        
        # Keep only the most recent summaries
        if len(self.memories["conversation_summaries"]) > 3:  # Store only the 3 most recent summaries
            self.memories["conversation_summaries"] = self.memories["conversation_summaries"][-3:]
            
        return summary_obj

    def get_memory_for_prompt(self, current_state):
        """Get relevant memories for the current therapy state to include in the prompt."""
        relevant_memories = {}
        
        # Always include personal info
        if self.memories["personal_info"]:
            relevant_memories["personal_info"] = self.memories["personal_info"]
        
        # Include focus intentions based on state
        if self.memories["focus_intentions"]:
            # In prelude, include all intentions to help establish focus
            if current_state == "prelude":
                relevant_memories["focus_intentions"] = self.memories["focus_intentions"]
            # In other states, just include the most recent 1-2
            else:
                relevant_memories["focus_intentions"] = self.memories["focus_intentions"][-2:]
        
        # Include emotional states
        if self.memories["emotional_states"]:
            # Get the most recent emotions
            relevant_memories["emotional_states"] = self.memories["emotional_states"][-3:]
            
        # Include imagery in music_imaging and postlude states
        if current_state in ["music_imaging", "postlude"] and self.memories["imagery"]:
            relevant_memories["imagery"] = self.memories["imagery"][-5:]
            
        # Include insights in music_imaging and postlude states
        if current_state in ["music_imaging", "postlude"] and self.memories["insights"]:
            relevant_memories["insights"] = self.memories["insights"][-3:]
            
        # Include music preferences in music_imaging state
        if current_state == "music_imaging" and self.memories["preferences"]["music"]:
            relevant_memories["music_preferences"] = self.memories["preferences"]["music"]
        
        # Include relevant conversation summaries
        if self.memories["conversation_summaries"]:
            relevant_memories["conversation_summaries"] = self.memories["conversation_summaries"][-2:]
            
        # Format the memories for inclusion in a prompt
        return self._format_memories_for_prompt(relevant_memories, current_state)
    
    def _format_memories_for_prompt(self, relevant_memories, current_state):
        """Format memories into a string for inclusion in a prompt."""
        memory_prompt = "CLIENT MEMORY INFORMATION:\n"
        
        # Format personal info
        if "personal_info" in relevant_memories:
            memory_prompt += "Personal Details:\n"
            for key, value in relevant_memories["personal_info"].items():
                memory_prompt += f"- {safe_text(key).capitalize()}: {safe_text(value)}\n"
        
        # Format focus intentions
        if "focus_intentions" in relevant_memories:
            memory_prompt += "\nFocus/Intentions:\n"
            for intention in relevant_memories["focus_intentions"][-3:]:  # Only the most recent ones
                focus_text = safe_text(intention.get("focus") if isinstance(intention, dict) else intention)
                memory_prompt += f"- {focus_text}\n"
        
        # Format emotional states
        if "emotional_states" in relevant_memories:
            memory_prompt += "\nRecent Emotional States:\n"
            for state in relevant_memories["emotional_states"]:
                emotion_text = safe_text(state.get("emotion") if isinstance(state, dict) else state)
                memory_prompt += f"- {emotion_text.capitalize()}\n"
        
        # Format imagery
        if "imagery" in relevant_memories:
            memory_prompt += "\nKey Imagery:\n"
            for image in relevant_memories["imagery"]:
                imagery_text = safe_text(image.get("description") if isinstance(image, dict) else image)
                memory_prompt += f"- {imagery_text}\n"
        
        # Format insights
        if "insights" in relevant_memories:
            memory_prompt += "\nClient Insights:\n"
            for insight in relevant_memories["insights"]:
                insight_text = safe_text(insight.get("content") if isinstance(insight, dict) else insight)
                memory_prompt += f"- {insight_text}\n"
        
        # Format music preferences
        if "music_preferences" in relevant_memories:
            memory_prompt += "\nMusic Preferences:\n"
            for pref in relevant_memories["music_preferences"]:
                sentiment = "Likes" if isinstance(pref, dict) and pref.get("sentiment") == "like" else "Dislikes"
                genre_text = safe_text(pref.get("genre") if isinstance(pref, dict) else pref)
                memory_prompt += f"- {sentiment}: {genre_text}\n"
        
        # Format conversation summaries
        if "conversation_summaries" in relevant_memories:
            memory_prompt += "\nConversation Context:\n"
            for summary in relevant_memories["conversation_summaries"]:
                memory_prompt += "Previous discussion summary:\n"
                summary_content = summary.get("content", {}) if isinstance(summary, dict) else {}
                if not isinstance(summary_content, dict):
                    summary_content = {}

                if "personal_info" in summary_content and summary_content["personal_info"]:
                    memory_prompt += "- Personal Info: " + safe_text(summary_content["personal_info"]) + "\n"
                
                if "concerns" in summary_content and summary_content["concerns"]:
                    memory_prompt += "- Concerns: " + safe_join(summary_content["concerns"]) + "\n"
                
                if "emotions" in summary_content and summary_content["emotions"]:
                    memory_prompt += "- Emotions: " + safe_join(summary_content["emotions"]) + "\n"
                
                if "topics" in summary_content and summary_content["topics"]:
                    memory_prompt += "- Topics: " + safe_join(summary_content["topics"]) + "\n"
                
                if "imagery" in summary_content and summary_content["imagery"]:
                    memory_prompt += "- Imagery: " + safe_join(summary_content["imagery"]) + "\n"
        
        # Add guidance based on the current state
        memory_prompt += f"\nUse this information to personalize the {safe_text(current_state)} phase of the therapy session. Refer to client details naturally in your responses.\n"
        
        print("!!!!!format Memory prompt:")
        print(memory_prompt)

        return memory_prompt
    
    def save_memories_to_file(self, filename="client_memories.json"):
        """Save the current memories to a JSON file."""
        try:
            with open(filename, 'w') as f:
                json.dump(self.memories, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving memories: {str(e)}")
            return False
    
    def load_memories_from_file(self, filename="client_memories.json"):
        """Load memories from a JSON file."""
        try:
            with open(filename, 'r') as f:
                loaded_memories = json.load(f)
                
            # Update memories with loaded data
            for key in self.memories:
                if key in loaded_memories:
                    self.memories[key] = loaded_memories[key]
            return True
        except FileNotFoundError:
            print(f"Memory file {filename} not found. Starting with fresh memory.")
            return False
        except Exception as e:
            print(f"Error loading memories: {str(e)}")
            return False
    
    def stop_processing(self):
        """Stop background processing threads"""
        self.is_processing_active = False
        if self.memory_thread.is_alive():
            self.memory_thread.join(timeout=2)
        if self.summary_thread.is_alive():
            self.summary_thread.join(timeout=2)


# Example usage
if __name__ == "__main__":
    memory = MemoryManager()
    
    # Example processing of a user message
    memory.process_message("Hi, my name is Alice and I'm feeling anxious today. I want to find some inner peace.")
    
    # Example processing of a therapist message
    memory.process_message("I'm experiencing a vivid forest in my imagination, with golden light filtering through the trees.", 
                           "Let's focus on finding your inner sanctuary.")
    
    # Get memories for prompt in the prelude phase
    prompt_memories = memory.get_memory_for_prompt("prelude")
    print(prompt_memories)
    
    # Save memories
    memory.save_memories_to_file() 

