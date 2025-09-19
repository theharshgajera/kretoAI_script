import streamlit as st
from dotenv import load_dotenv
import os
import google.generativeai as genai
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound, VideoUnavailable
import time
import re
from urllib.parse import urlparse, parse_qs

# Load environment variables
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

prompt = """You are a YouTube video summarizer. You will be taking the transcript text
and summarizing the entire video and providing the important summary in points
within 250 words. Please provide the summary of the text given here: """

def extract_video_id(youtube_url):
    """Extract video ID from various YouTube URL formats"""
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
        r'youtube\.com\/watch\?.*v=([^&\n?#]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, youtube_url)
        if match:
            return match.group(1)
    
    return None

def extract_transcript_details(youtube_video_url, max_retries=3, retry_delay=2):
    """Extract transcript with proper API usage and rate limiting"""
    
    video_id = extract_video_id(youtube_video_url)
    if not video_id:
        return "Error: Invalid YouTube URL format."
    
    for attempt in range(max_retries):
        try:
            # Add progressive delay to avoid rate limiting
            if attempt > 0:
                wait_time = retry_delay * (attempt + 1)
                st.info(f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            
            # Create API instance
            ytt_api = YouTubeTranscriptApi()
            
            # Method 1: Try direct fetch (easiest way)
            try:
                fetched_transcript = ytt_api.fetch(video_id, languages=['en', 'en-US', 'en-GB'])
                transcript_text = " ".join([snippet.text for snippet in fetched_transcript])
                
                if len(transcript_text.strip()) >= 50:
                    return transcript_text
                    
            except NoTranscriptFound:
                pass  # Try method 2
            
            # Method 2: List transcripts and find the best one
            try:
                transcript_list = ytt_api.list(video_id)
                
                # Try to find English transcript first
                try:
                    transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
                    fetched_transcript = transcript.fetch()
                except NoTranscriptFound:
                    try:
                        # If English not found, try any manually created transcript
                        transcript = transcript_list.find_manually_created_transcript(['en', 'en-US', 'en-GB'])
                        fetched_transcript = transcript.fetch()
                    except NoTranscriptFound:
                        try:
                            # If no manual transcript, try auto-generated
                            transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                            fetched_transcript = transcript.fetch()
                        except NoTranscriptFound:
                            # Last resort: get any available transcript
                            available_transcripts = list(transcript_list)
                            if available_transcripts:
                                transcript = available_transcripts[0]
                                fetched_transcript = transcript.fetch()
                            else:
                                return "Error: No transcripts available for this video."
                
                # Convert transcript data to text
                transcript_text = " ".join([snippet.text for snippet in fetched_transcript])
                
                # Check if transcript is meaningful
                if len(transcript_text.strip()) < 50:
                    return "Error: Transcript too short or incomplete."
                    
                return transcript_text
                
            except Exception as inner_e:
                if attempt == max_retries - 1:
                    return f"Error: Could not access transcript list - {str(inner_e)}"
                continue

        except VideoUnavailable:
            return "Error: Video is unavailable, private, or doesn't exist."
        except TranscriptsDisabled:
            return "Error: Transcripts are disabled for this video."
        except Exception as e:
            error_msg = str(e).lower()
            
            # Handle various API errors
            if "quota" in error_msg or "rate" in error_msg or "429" in error_msg:
                if attempt < max_retries - 1:
                    st.warning(f"Rate limit hit. Waiting {retry_delay * (attempt + 2)} seconds before retry...")
                    continue
                else:
                    return "Error: API rate limit exceeded. Please try again in a few minutes."
            elif "403" in error_msg or "forbidden" in error_msg:
                return "Error: Access forbidden. Video might be private or restricted."
            elif "404" in error_msg:
                return "Error: Video not found. Please check the URL."
            elif "blocked" in error_msg or "ipblocked" in error_msg:
                return "Error: IP address blocked by YouTube. Try using a VPN or proxy."
            elif attempt == max_retries - 1:
                return f"Error fetching transcript after {max_retries} attempts: {str(e)}"
    
    return "Error: Failed to fetch transcript after multiple attempts."

def generate_gemini_content(transcript_text, prompt, max_retries=3):
    """Generate summary with retry logic for API failures"""
    
    for attempt in range(max_retries):
        try:
            # Add delay between attempts
            if attempt > 0:
                wait_time = 3 * attempt
                st.info(f"AI API retry in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            
            model = genai.GenerativeModel("gemini-2.0-flash")
            
            # Split long transcripts into chunks if needed
            max_chars = 30000  # Conservative limit for Gemini
            if len(transcript_text) > max_chars:
                # Take first chunk for summary
                transcript_text = transcript_text[:max_chars] + "..."
                st.warning("Transcript was too long and has been truncated for processing.")
            
            response = model.generate_content(prompt + transcript_text)
            
            if response.text:
                return response.text
            else:
                return "Error: Empty response from Gemini API."
                
        except Exception as e:
            error_msg = str(e).lower()
            
            if "quota" in error_msg or "rate" in error_msg:
                if attempt < max_retries - 1:
                    st.warning(f"Gemini API rate limit. Waiting {3 * (attempt + 2)} seconds...")
                    continue
                else:
                    return "Error: Gemini API quota exceeded. Please try again later."
            elif "safety" in error_msg:
                return "Error: Content flagged by safety filters. Try a different video."
            elif attempt == max_retries - 1:
                return f"Error generating summary after {max_retries} attempts: {str(e)}"
    
    return "Error: Failed to generate summary after multiple attempts."

def validate_youtube_url(url):
    """Validate if the URL is a proper YouTube URL"""
    youtube_domains = ['youtube.com', 'youtu.be', 'www.youtube.com']
    try:
        parsed_url = urlparse(url)
        return any(domain in parsed_url.netloc for domain in youtube_domains)
    except:
        return False

# Streamlit App
st.title("🎥 YouTube Transcript to Detailed Notes Converter")
st.markdown("Convert any YouTube video with captions into detailed summary notes!")

# Add sidebar with instructions
with st.sidebar:
    st.header("📝 Instructions")
    st.markdown("""
    1. Paste any YouTube video URL
    2. Click 'Get Detailed Notes'
    3. Wait for the AI to process and summarize
    
    **Supported URL formats:**
    - https://youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://youtube.com/embed/VIDEO_ID
    """)
    
    st.header("⚠️ Requirements")
    st.markdown("""
    - Video must have captions/subtitles
    - Video must be publicly accessible
    - Valid Google API key in .env file
    """)
    
    st.header("🔧 Troubleshooting")
    st.markdown("""
    **Common Issues:**
    - Rate limits: Wait a few minutes between requests
    - No transcript: Video has no captions
    - Private video: Can't access private/unlisted videos
    """)

youtube_link = st.text_input("Enter YouTube Video Link:", placeholder="https://youtube.com/watch?v=...")

if youtube_link:
    if validate_youtube_url(youtube_link):
        try:
            video_id = extract_video_id(youtube_link)
            if video_id:
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.image(f"http://img.youtube.com/vi/{video_id}/maxresdefault.jpg", width=400)
                with col2:
                    st.markdown(f"**Video ID:** `{video_id}`")
                    st.markdown(f"**Status:** ✅ Valid URL")
            else:
                st.error("Could not extract video ID from the URL")
        except Exception as e:
            st.warning("Could not load video thumbnail")
    else:
        st.error("❌ Please enter a valid YouTube URL")

# Add rate limiting notice
st.info("⏱️ **Note:** To avoid rate limits, please wait at least 30 seconds between requests.")

# Add progress tracking
if st.button("🚀 Get Detailed Notes", disabled=not youtube_link or not validate_youtube_url(youtube_link)):
    if youtube_link and validate_youtube_url(youtube_link):
        
        # Create progress bar and status
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Step 1: Extract transcript
        status_text.text("📥 Fetching transcript from YouTube...")
        progress_bar.progress(25)
        
        transcript_text = extract_transcript_details(youtube_link)
        
        if transcript_text.startswith("Error"):
            st.error(transcript_text)
            
            # Provide helpful suggestions based on error type
            if "rate limit" in transcript_text.lower():
                st.info("💡 **Suggestion:** Wait 2-3 minutes before trying again.")
            elif "no transcript" in transcript_text.lower():
                st.info("💡 **Suggestion:** Try a video with auto-generated captions or manual subtitles.")
            elif "private" in transcript_text.lower():
                st.info("💡 **Suggestion:** Use a public video URL.")
            
            progress_bar.empty()
            status_text.empty()
        else:
            progress_bar.progress(50)
            status_text.text("📊 Transcript fetched successfully! Generating summary...")
            
            # Show transcript stats
            word_count = len(transcript_text.split())
            char_count = len(transcript_text)
            
            with st.expander("📋 Transcript Statistics"):
                col1, col2, col3 = st.columns(3)
                col1.metric("Words", f"{word_count:,}")
                col2.metric("Characters", f"{char_count:,}")
                col3.metric("Est. Read Time", f"{word_count // 200 + 1} min")
            
            # Show a preview of the transcript
            with st.expander("👀 Transcript Preview (First 500 characters)"):
                st.text(transcript_text[:500] + "..." if len(transcript_text) > 500 else transcript_text)
            
            # Step 2: Generate summary
            progress_bar.progress(75)
            status_text.text("🤖 AI is analyzing and summarizing...")
            
            summary = generate_gemini_content(transcript_text, prompt)
            
            progress_bar.progress(100)
            status_text.text("✅ Summary generated successfully!")
            
            if summary.startswith("Error"):
                st.error(summary)
                
                # Provide suggestions for AI errors
                if "quota" in summary.lower():
                    st.info("💡 **Suggestion:** Wait an hour or check your Google API quotas.")
                elif "safety" in summary.lower():
                    st.info("💡 **Suggestion:** Try a different video with educational content.")
                
            else:
                st.success("🎉 Notes generated successfully!")
                
                # Display results
                st.markdown("## 📝 Detailed Notes:")
                st.markdown("---")
                st.write(summary)
                
                # Add download option
                st.download_button(
                    label="📥 Download Notes as Text",
                    data=summary,
                    file_name=f"youtube_notes_{video_id}.txt",
                    mime="text/plain"
                )
                
                # Add copy to clipboard (JavaScript will handle this)
                st.code(summary, language="text")
            
            # Clean up progress indicators
            time.sleep(1)
            progress_bar.empty()
            status_text.empty()

# Add footer with tips
st.markdown("---")
st.markdown("""
💡 **Tips for Best Results:**
- Use videos with clear speech and good audio quality
- Educational content works better than music videos
- Longer videos (5+ minutes) typically provide better summaries
- Wait 30 seconds between requests to avoid rate limits

🔗 **Need Help?** Check that your `.env` file contains: `GOOGLE_API_KEY=your_api_key_here`
""")

# Add current time for debugging
if st.checkbox("Show Debug Info"):
    st.write(f"Current time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    st.write(f"Streamlit version: {st.__version__}")