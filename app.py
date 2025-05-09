import http.client
import json
import os
import urllib.parse
from datetime import datetime
import streamlit as st
import schedule
import time
from fuzzywuzzy import fuzz
import requests
import threading

# API configurations
ADZUNA_APP_ID = st.secrets["ADZUNA_APP_ID"]
ADZUNA_API_KEY = st.secrets["ADZUNA_API_KEY"]
RESEND_API_KEY = st.secrets["RESEND_API_KEY"]

class JobMatchmaker:
    def __init__(self):
        self.users = []
        self.job_cache = {}
    
    def add_user(self, name, email, location, roles, skills, min_salary):
        user = {
            "name": name,
            "email": email,
            "location": location,
            "roles": [role.strip() for role in roles.split(',')],
            "skills": [skill.strip().lower() for skill in skills.split(',')],
            "min_salary": min_salary,
            "last_notified": None
        }
        self.users.append(user)
        return f"Added user: {name}"
    
    def fetch_jobs(self, query, location, country="gb"):
        # Check cache first
        cache_key = f"{query}_{location}_{country}"
        if cache_key in self.job_cache:
            # Return cached results if less than 24 hours old
            if (datetime.now() - self.job_cache[cache_key]["timestamp"]).total_seconds() < 86400:
                return self.job_cache[cache_key]["data"]
        
        # Prepare parameters
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_API_KEY,
            "results_per_page": 20,
            "what": query,
            "where": location,
            "content-type": "application/json"
        }
        
        # Make API request
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        response = requests.get(url, params=params)
        data = response.json()
        
        # Cache the results
        self.job_cache[cache_key] = {
            "timestamp": datetime.now(),
            "data": data
        }
        
        return data
    
    def match_jobs_for_user(self, user):
        matched_jobs = []
        
        for role in user["roles"]:
            jobs_data = self.fetch_jobs(role, user["location"])
            
            if "results" not in jobs_data:
                continue
                
            for job in jobs_data["results"]:
                score = 0
                
                # Match job title
                if "title" in job:
                    title_score = fuzz.token_set_ratio(role.lower(), job["title"].lower())
                    score += title_score * 0.3
                
                # Match skills
                if "description" in job:
                    skill_score = 0
                    for skill in user["skills"]:
                        if skill.lower() in job["description"].lower():
                            skill_score += 10
                    score += min(skill_score, 50)
                
                # Match location
                if "location" in job and "area" in job["location"]:
                    location_str = ", ".join(job["location"]["area"])
                    location_score = fuzz.token_set_ratio(user["location"].lower(), location_str.lower())
                    score += location_score * 0.2
                
                # Check salary if available
                if "salary_min" in job and job["salary_min"] is not None:
                    if job["salary_min"] >= user["min_salary"]:
                        score += 10
                
                if score > 60:  # Only include jobs with good match
                    matched_jobs.append({
                        "job": job,
                        "score": score
                    })
        
        # Sort by score and return top 5
        matched_jobs.sort(key=lambda x: x["score"], reverse=True)
        return matched_jobs[:5]
    
    def send_email_notification(self, user, matched_jobs):
        if not matched_jobs:
            return "No matching jobs found"
        
        # Build email content
        email_body = f"Hello {user['name']},\n\nHere are your personalized job matches:\n\n"
        
        for match in matched_jobs:
            job = match["job"]
            email_body += f"- {job.get('title', 'Untitled Position')} at {job.get('company', {}).get('display_name', 'Unknown Company')}\n"
            if "location" in job and "area" in job["location"]:
                email_body += f"  Location: {', '.join(job['location']['area'])}\n"
            email_body += f"  Match Score: {match['score']:.1f}%\n"
            if "redirect_url" in job:
                email_body += f"  Apply here: {job['redirect_url']}\n"
            email_body += "\n"
        
        # Use Resend API to send email
        conn = http.client.HTTPSConnection("api.resend.com")
        
        payload = json.dumps({
            "from": "jobs@yourdomain.com",
            "to": user["email"],
            "subject": "Your Personalized Job Matches",
            "text": email_body
        })
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {RESEND_API_KEY}'
        }
        
        conn.request("POST", "/emails", payload, headers)
        
        res = conn.getresponse()
        data = res.read()
        
        user["last_notified"] = datetime.now()
        return f"Notification sent to {user['email']}"
    
    def run_matching_for_all_users(self):
        results = []
        for user in self.users:
            matched_jobs = self.match_jobs_for_user(user)
            result = self.send_email_notification(user, matched_jobs)
            results.append(f"{user['name']}: {result}")
        return "\n".join(results)

    def search_available_jobs(self, query, location, country="gb"):
        """Search for available jobs and return formatted results"""
        jobs_data = self.fetch_jobs(query, location, country)
        
        if "results" not in jobs_data or not jobs_data["results"]:
            return "No jobs found for the given criteria."
        
        results = []
        for job in jobs_data["results"][:10]:  # Limit to top 10 results
            job_info = f"**{job.get('title', 'Untitled Position')}** at {job.get('company', {}).get('display_name', 'Unknown Company')}\n"
            if "location" in job and "area" in job["location"]:
                job_info += f"Location: {', '.join(job['location']['area'])}\n"
            if "salary_min" in job and "salary_max" in job:
                job_info += f"Salary: {job.get('salary_min')} - {job.get('salary_max')} per {job.get('salary_is_predicted', 'year')}\n"
            if "redirect_url" in job:
                job_info += f"Apply: {job.get('redirect_url')}\n"
            if "description" in job:
                # Truncate description to first 150 characters
                desc = job["description"][:150] + "..." if len(job["description"]) > 150 else job["description"]
                job_info += f"Description: {desc}\n"
            job_info += "---\n"
            results.append(job_info)
        
        return "\n".join(results)

# Initialize the matchmaker
matchmaker = JobMatchmaker()

# Schedule daily job matching
def scheduled_job():
    matchmaker.run_matching_for_all_users()

schedule.every().day.at("09:00").do(scheduled_job)

# Run the scheduler in a separate thread
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)

scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.daemon = True
scheduler_thread.start()

# Streamlit app
st.set_page_config(page_title="Job Matchmaker Agent", layout="wide")
st.title("Job Matchmaker Agent")

# Create tabs
tab1, tab2, tab3 = st.tabs(["Add User", "Run Matching", "Search Available Jobs"])

# Tab 1: Add User
with tab1:
    st.header("Add User")
    name = st.text_input("Name")
    email = st.text_input("Email")
    location = st.text_input("Location (City, Country)")
    roles = st.text_input("Preferred Job Roles (comma-separated)")
    skills = st.text_input("Skills (comma-separated)")
    min_salary = st.number_input("Minimum Salary", min_value=0)
    
    if st.button("Add User"):
        result = matchmaker.add_user(name, email, location, roles, skills, min_salary)
        st.success(result)

# Tab 2: Run Matching
with tab2:
    st.header("Run Job Matching")
    if st.button("Run Job Matching"):
        results = matchmaker.run_matching_for_all_users()
        st.text_area("Results", results, height=300)

# Tab 3: Search Available Jobs
with tab3:
    st.header("Search for available jobs")
    job_title = st.text_input("Job Title")
    job_location = st.text_input("Location")
    country = st.selectbox(
        "Country", 
        options=["gb", "us", "au", "br", "ca", "de", "fr", "in", "it", "nl", "nz", "pl", "ru", "sg", "za"],
        index=0
    )
    
    if st.button("Search Jobs"):
        results = matchmaker.search_available_jobs(job_title, job_location, country)
        st.markdown(results)

# Launch the Streamlit app
if __name__ == "__main__":
    st.run()



