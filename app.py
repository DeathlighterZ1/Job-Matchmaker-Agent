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

print(f"API credentials loaded: Adzuna ID={ADZUNA_APP_ID[:4]}..., Key={ADZUNA_API_KEY[:4]}..., Resend={RESEND_API_KEY[:4]}...")

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
        
        try:
            # Make API request
            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
            print(f"Making request to: {url} with params: {params}")
            response = requests.get(url, params=params)
            print(f"Response status code: {response.status_code}")
            response.raise_for_status()  # Raise exception for HTTP errors
            data = response.json()
            print(f"Response data: {data.keys()}")
            
            # Cache the results
            self.job_cache[cache_key] = {
                "timestamp": datetime.now(),
                "data": data
            }
            
            return data
        except Exception as e:
            error_msg = f"Error fetching jobs: {str(e)}"
            print(error_msg)
            return {"results": [], "error": error_msg}
    
    def match_jobs_for_user(self, user):
        matched_jobs = []
        
        for role in user["roles"]:
            jobs_data = self.fetch_jobs(role, user["location"])
            
            if "results" not in jobs_data:
                continue
            
            for job in jobs_data["results"]:
                score = 0
                matched_skills = []
                
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
                            matched_skills.append(skill)
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
                        "score": score,
                        "matched_skills": matched_skills
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
            if match["matched_skills"]:
                email_body += f"  Matched Skills: {', '.join(match['matched_skills'])}\n"
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
        if not query or not location:
            return "Please provide both job title and location."
        
        st.write(f"Searching for '{query}' in '{location}' ({country})...")
        jobs_data = self.fetch_jobs(query, location, country)
        
        if "error" in jobs_data:
            return f"Error: {jobs_data['error']}"
        
        if "results" not in jobs_data or not jobs_data["results"]:
            return "No jobs found for the given criteria."
        
        st.write(f"Found {len(jobs_data['results'])} jobs")
        
        results = []
        for job in jobs_data["results"][:10]:  # Limit to top 10 results
            # Create a more structured and visually appealing job card
            job_info = f"""
            <div style="padding: 15px; margin-bottom: 20px; border-radius: 8px; border: 1px solid #ddd; background-color: #f9f9f9;">
                <h3 style="color: #2c3e50; margin-top: 0;">{job.get('title', 'Untitled Position')}</h3>
                <h4 style="color: #3498db; margin-top: 5px;">{job.get('company', {}).get('display_name', 'Unknown Company')}</h4>
                
                <div style="margin: 10px 0;">
            """
            
            if "location" in job and "area" in job["location"]:
                job_info += f"<p><strong>📍 Location:</strong> {', '.join(job['location']['area'])}</p>"
            
            if "salary_min" in job and "salary_max" in job:
                salary_period = job.get('salary_is_predicted', 'year')
                if salary_period == "1":
                    salary_period = "year"
                job_info += f"<p><strong>💰 Salary:</strong> ${job.get('salary_min'):,.2f} - ${job.get('salary_max'):,.2f} per {salary_period}</p>"
            
            if "description" in job:
                # Truncate description to first 150 characters
                desc = job["description"][:150] + "..." if len(job["description"]) > 150 else job["description"]
                job_info += f"<p><strong>📝 Description:</strong> {desc}</p>"
            
            if "redirect_url" in job:
                job_info += f'<p><a href="{job.get("redirect_url")}" target="_blank" style="background-color: #3498db; color: white; padding: 8px 15px; text-decoration: none; border-radius: 4px; display: inline-block; margin-top: 10px;">Apply Now</a></p>'
            
            job_info += "</div></div>"
            results.append(job_info)
        
        return "".join(results)

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
    
    if not matchmaker.users:
        st.warning("No users added yet. Please add users in the 'Add User' tab first.")
    else:
        # Display user selection dropdown
        user_names = [user["name"] for user in matchmaker.users]
        selected_user = st.selectbox("Select User", user_names)
        
        # Get the selected user
        user = next((u for u in matchmaker.users if u["name"] == selected_user), None)
        
        if user:
            # Display user's preferred roles as selectable options
            st.subheader("Preferred Job Roles")
            selected_role = st.selectbox("Select role to search", user["roles"])
            
            if st.button("Find Matching Jobs", type="primary"):
                with st.spinner(f"Searching for '{selected_role}' jobs in '{user['location']}'..."):
                    results = matchmaker.search_available_jobs(selected_role, user["location"])
                    
                    if results == "No jobs found for the given criteria.":
                        st.error("No jobs found. Please try different search terms.")
                    else:
                        st.markdown(results, unsafe_allow_html=True)

# Tab 3: Search Available Jobs
with tab3:
    st.header("Search for available jobs")
    col1, col2 = st.columns(2)
    with col1:
        job_title = st.text_input("Job Title")
    with col2:
        job_location = st.text_input("Location")
    
    country = st.selectbox(
        "Country", 
        options=["gb", "us", "au", "br", "ca", "de", "fr", "in", "it", "nl", "nz", "pl", "ru", "sg", "za"],
        index=0
    )
    
    if st.button("Search Jobs", type="primary"):
        with st.spinner("Searching for jobs..."):
            results = matchmaker.search_available_jobs(job_title, job_location, country)
            
            if results == "No jobs found for the given criteria.":
                st.error("No jobs found. Please try different search terms.")
            else:
                st.markdown(results, unsafe_allow_html=True)

# Launch the Streamlit app
if __name__ == "__main__":
    pass  # Streamlit automatically runs the app







