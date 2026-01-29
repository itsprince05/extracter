import requests
import json
from bs4 import BeautifulSoup
import urllib.parse
import sys

def get_instagram_media_links(instagram_url):
    """
    Takes an Instagram post URL, queries media.mollygram.com,
    and returns a list of media download URLs found in the response.
    """
    
    # Base API URL
    base_url = "https://media.mollygram.com/"
    
    # Prepare parameters
    params = {
        'url': instagram_url
    }
    
    # Add headers to mimic a browser, often helps with scraping/API requests
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        # Make the request
        print(f"Fetching data for: {instagram_url}...")
        response = requests.get(base_url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse JSON response
        try:
            data = response.json()
        except json.JSONDecodeError:
            print("Error: content is not valid JSON.")
            return []

        if data.get("status") != "ok":
            print(f"Error from API: {data.get('status')}")
            return []

        html_content = data.get("html", "")
        if not html_content:
            print("No HTML content found in response.")
            return []

        # Parse HTML to find download links
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Strategy: Look for the download buttons.
        # Based on user input, these are <a> tags with id="download-video" (often repeated)
        # or class containing "btn bg-gradient-success"
        
        media_links = []
        
        # Finding all anchors with id 'download-video'. 
        # Note: HTML standards say IDs should be unique, but parsers like BS4 handle duplicates fine.
        download_buttons = soup.find_all('a', id='download-video')
        
        # Fallback: if finding by ID doesn't work or we want to be more robust, 
        # we can look for specific classes or text.
        if not download_buttons:
             download_buttons = soup.find_all('a', class_='bg-gradient-success')

        for btn in download_buttons:
            href = btn.get('href')
            if href:
                media_links.append(href)

        return media_links

    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return []

if __name__ == "__main__":
    # Check if URL is provided via command line args
    if len(sys.argv) > 1:
        url_input = sys.argv[1]
        links = get_instagram_media_links(url_input)
        print("\nFound Media Links:")
        for link in links:
            print(link)
    else:
        # Default test urls from the prompt
        test_urls = [
            "https://www.instagram.com/p/CmmOYtXhO4Q/?igshid=YmMyMTA2M2Y=",
            "https://www.instagram.com/reel/CmlENuHjRGy/?igshid=YmMyMTA2M2Y=",
            "https://www.instagram.com/p/Cmoh_jYvJwK/?igshid=YmMyMTA2M2Y="
        ]
        
        print("No URL provided. Running tests with example URLs...\n")
        
        for url in test_urls:
            print(f"Testing URL: {url}")
            links = get_instagram_media_links(url)
            print(f"Found {len(links)} link(s):")
            for i, link in enumerate(links, 1):
                print(f"{i}: {link}")
            print("-" * 30)
