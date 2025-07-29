import xml.etree.ElementTree as ET
import requests
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

sessions = {}

def get_session(msisdn):
    """Get or create a session for the given msisdn."""
    if msisdn not in sessions:
        sessions[msisdn] = {} 
    return sessions[msisdn]
    
def check_if_user_registered(msisdn):
    """Checks if the user is already registered in the system."""
    registration_check_url = f"https://chiweto.ch/insurance/api/is_registered_ussd?msisdn={msisdn}"
    # registration_check_url = f"https://chiweto.ch/insurance/api/IsRegisteredUssd?msisdn={msisdn}"
    try:
        response = requests.get(registration_check_url)
        # response = requests.get(registration_check_url, 
        #                         # params={'msisdn': msisdn}, 
        #                         headers={
        #                             'Accept': 'application/json',
        #                             # "Authorization":  "ce798deddba8a7f24e5030c54d37dc63a1b2657868a972b3785de53ab3414b8e",
        #                             },
        #                         timeout=10)
        
        if response.status_code == 200:
            # Extract registration status from the response
            response_data = response.json()
            return response_data.get('is_registered', False)
        else:
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error checking registration status: {e}")
        return False
    
def validate_pin(msisdn, pin):
    """Validate the entered PIN for the registered user using the Laravel API."""
    pin_validation_url = "https://chiweto.ch/insurance/api/UssdAuthentication"
    #pin_validation_url = "https://chiweto.ch/insurance/api/check_login"
    
    try:
        payload = {
            'msisdn': msisdn,
            'pin': pin
        }
        response = requests.post(pin_validation_url, json=payload, verify=True)  
        response.raise_for_status()
        print(f"API Response: {response.json()}")
        if response.json().get('success', False):
            return True
        else:
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error validating PIN: {e}")
        return False
    except requests.exceptions.HTTPError as err:
        # Log any specific HTTP error
        print(f"API error: {err}")
        return False
        
    except requests.exceptions.RequestException as e:
        print(f"Error validating PIN: {e}")
        return False, None
    except requests.exceptions.HTTPError as err:
        print(f"API error: {err}")
        return False, None
    
@csrf_exempt
def handle_ussd(request):
    # Parse the incoming XML
    tree = ET.ElementTree(ET.fromstring(request.body))
    root = tree.getroot()

    msisdn = root.find('msisdn').text  # Subscriber's phone number
    session_id = root.find('sessionid').text  # Session ID
    request_type = int(root.find('type').text)  # Type of request (1 for new session, 2 for existing)
    msg = root.find('msg').text  # User input or service code

    session = request.session  # Django session for storing step-by-step data
    current_step = session.get('current_step', 1)  # Default step is 1 (Name input)
    
    # Handle navigation: back or home
    if msg == '0':  # Back to previous step
        current_step = max(1, current_step - 1)
        session['current_step'] = current_step
        return handle_back_step(current_step, session)

    if msg == '00':  # Return to home (step 1)
        current_step = 1
        session['current_step'] = current_step
        return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):\n0. Back\n00. Home", 2)
     # New session
    if request_type == 1:
         # Check user registration status
        user_registered = check_if_user_registered(msisdn)
        if user_registered:
            current_step = 'request_pin'  # Existing user, prompt for PIN
            session['current_step'] = current_step
            return generate_response_xml("Mwalandiridwa ku Chiweto. \n Lowetsani nambala yachinsisi:", 2)
        else:
            current_step = 1  # New user, start registration process
            session['current_step'] = current_step
            return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):", 2)

    # Handle existing session and user input
    if request_type == 2:
        if current_step == 'request_pin':
            pin = msg
            print(f"Received PIN input: {pin}")
            if validate_pin(msisdn, pin):
                # Valid PIN, show registered user menu
                return handle_registered_user_menu(session)
            else:
                # Invalid PIN
                return generate_response_xml("Invalid PIN. Please try again:\n{}:{}".format(msisdn, pin), 2)
        # Check the current step to handle the flow
        elif current_step == 1:
            # Step 1: Name Input
            farmer_name = msg  # User entered their name
            session['farmer_name'] = farmer_name  # Save the name in the session
            current_step = 2
            session['current_step'] = current_step
            return generate_response_xml("Ndinu mamuna kapena mkazi:\n1. Mamuna\n2. Mkazi\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

        elif current_step == 2:
            # Step 2: Gender Selection
            if msg == '1':
                farmer_gender = 'Male'
            elif msg == '2':
                farmer_gender = 'Female'
            else:
                return generate_response_xml("Mwasankha njira yolakwika. Ndinu mamuna kapena mkazi :\n1. Male\n2. Female", 2)
            session['farmer_gender'] = farmer_gender  # Save the gender
            current_step = 3
            session['current_step'] = current_step
            return fetch_regions_and_respond(session)

        elif current_step == 3:
            # Step 3: Region Selection987654
            regions = session.get('regions', [])
            selected_region_idx = int(msg) - 1
            if selected_region_idx >= 0 and selected_region_idx < len(regions):
                selected_region = regions[selected_region_idx]  # Get the selected region
                session['selected_region'] = selected_region  # Save selected region
                current_step = 4
                session['current_step'] = current_step
                return fetch_districts_and_respond(selected_region, session)
            else:
                return generate_response_xml("Mwasankha chigawo cholakwika. Chonde yesaninso.", 2)

        elif current_step == 4:
            # Step 4: District Selection
            districts = session.get('districts', [])
            selected_district_idx = int(msg) - 1
            if selected_district_idx >= 0 and selected_district_idx < len(districts):
                selected_district = districts[selected_district_idx]  # Get the selected district
                session['selected_district'] = selected_district  # Save selected district
                current_step = 5
                session['current_step'] = current_step
                return fetch_epas_and_respond(selected_district, session)
            else:
                return generate_response_xml("Mwasankha boma lokwakwika. Chonde yesaninso.", 2)

        elif current_step == 5:
            # Step 5: EPA Selection
            # In the main handle_ussd function, add navigation handling for EPA selection
            if current_step == 5:
                # Step 5: EPA Selection with pagination
                if msg in ['8', '9']:
                    return handle_epa_navigation(msg, session)  # Handle page navigation

                epas = session.get('epas', [])
                selected_epa_idx = int(msg) - 1
                if selected_epa_idx >= 0 and selected_epa_idx < len(epas):
                    selected_epa = epas[selected_epa_idx]  # Get the selected EPA
                    session['selected_epa'] = selected_epa  # Save selected EPA
                    return submit_farmer_registration(session, msisdn)
                else:
                    return generate_response_xml("Mwasankha Dela lolakwika. Chonde yesaninso.", 2)
                
        # Handle logic for registered user options
    if current_step == 'registered_menu':
        if msg == '1':
            # Option 1: Gulani Inshulansi
            session['current_step'] = 'buy_insurance'
            return fetch_livestocks_and_respond(session)
            # return generate_response_xml("Mukhoza kugula inshulansi yomwe:\n1. Inshulansi ya Ziweto\n0. Bwelerani", 2)
        elif msg == '2':
            # Option 2: Itanani Mlangizi
            session['current_step'] = 'call_advisor'
            return generate_response_xml("Chonde lowetsani nambala ya mlangizi amene mukufuna kulankhula naye:", 2)
        elif msg == '3':
            # Option 3: View Policies
            session['current_step'] = 'view_policies'
            return fetch_policies_and_respond(msisdn)
        elif msg == '4':
            # Option 4: Logout (end session)
            session['current_step'] = 'end'
            return generate_response_xml("Zikomo pogwiritsa ntchito chiweto. Tsalani bwino.", 3)  # End of session
        else:
            return generate_response_xml("Chonde sankhani njira yovomerezeka:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Tulukani", 2)
        
    elif current_step == 'buy_insurance':
        # Handle livestock selection
        if msg == '0':
            session['current_step'] = 'registered_menu'
            return generate_response_xml("Returning to the previous menu.", 2)

        elif msg == '00':
            session['current_step'] = 'main_menu'
            return generate_response_xml("Returning to the main menu.", 2)

        elif msg.isdigit() and 1 <= int(msg) <= len(session.get('livestock', [])):
            # User selects a livestock, fetch the corresponding id
            selected_livestock_index = int(msg) - 1
            selected_livestock = session['livestock_data'][selected_livestock_index]  # Get the full livestock data
            selected_livestock_id = selected_livestock['id']  # Extract the id of the selected livestock

            session['selected_livestock_id'] = selected_livestock_id  # Store the id in session
            session['selected_livestock'] = selected_livestock['description']  # Optionally store description

            # Move to insurance selection step
            session['current_step'] = 'buy_insurance_select'
            return fetch_insurance_types_and_respond(session)  # Fetch insurance types based on selected livestock

        else:
            livestock_list = session.get('livestock', [])
            response_message = "Select Livestock:\n"
            for idx, livestock in enumerate(livestock_list, 1):
                response_message += f"{idx}. {livestock}\n"
            response_message += "0. Back\n00. Main Menu"
            return generate_response_xml(response_message, 2)

    elif current_step == 'buy_insurance_select':
        if msg == '0':
            session['current_step'] = 'registered_menu'
            return generate_response_xml("Returning to the previous menu.", 2)

        elif msg == '00':
            session['current_step'] = 'main_menu'
            return generate_response_xml("Returning to the main menu.", 2)

        elif msg.isdigit() and 1 <= int(msg) <= len(session.get('insurance', [])):
            # User selects an insurance type
            selected_insurance_index = int(msg) - 1  # Convert user input to index
            selected_insurance = session['insurance_data'][selected_insurance_index]  # Get full insurance data
            selected_insurance_id = selected_insurance['id']  # Get the id of the selected insurance

            session['selected_insurance_id'] = selected_insurance_id  # Store the selected insurance id
            session['selected_insurance'] = selected_insurance['description']  # Optionally store the name

            msisdn = session.get('phone_number', msisdn)
            return submit_insurance_data(session, msisdn)

        else:
            insurance_list = session.get('insurance', [])
            response_message = "Select an insurance type:\n"
            for idx, insurance in enumerate(insurance_list, 1):
                response_message += f"{idx}. {insurance}\n"
            response_message += "0. Back\n00. Main Menu"
            return generate_response_xml(response_message, 2)
    
def handle_registered_user_menu(session):
    """Handle menu options for already registered users."""
    response_message = "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Tulukani"
    session['current_step'] = 'registered_menu'
    return generate_response_xml(response_message, 2)
            
# Helper functions for each step and API requests
def generate_response_xml(message, response_type, **kwargs):
    """Helper to generate XML response."""
    xml_response = ET.Element('ussd')
    ET.SubElement(xml_response, 'type').text = str(response_type)
    ET.SubElement(xml_response, 'msg').text = message
    for key, value in kwargs.items():
        ET.SubElement(xml_response, key).text = str(value)

    return HttpResponse(ET.tostring(xml_response, encoding='utf-8').decode('utf-8'), content_type='text/xml', status=200)

def handle_back_step(current_step, session):
    """Handles going back to the previous step."""
    if current_step == 1:
        return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
    elif current_step == 2:
        return generate_response_xml("Ndinu mamuna kapena mkazi:\n1. Male\n2. Female\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
    elif current_step == 3:
        regions = session.get('regions', [])
        response_message = "Sakhani Chigawo Chomwe mumakhala:\n"
        for idx, region in enumerate(regions, 1):
            response_message += f"{idx}. {region}\n"
        response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
        return generate_response_xml(response_message, 2)
    elif current_step == 4:
        districts = session.get('districts', [])
        response_message = "Sakhani Boma lomwe mukukhala:\n"
        for idx, district in enumerate(districts, 1):
            response_message += f"{idx}. {district}\n"
        response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
        return generate_response_xml(response_message, 2)

def fetch_regions_and_respond(session):
    """Calls the Laravel API to get regions and responds to the user."""
    laravel_url = "https://chiweto.ch/insurance/api/regions"
    try:
        api_response = requests.get(laravel_url, verify=False)  # Disable SSL verification temporarily
        if api_response.status_code == 200:
            regions = api_response.json()
            session['regions'] = regions  # Store regions in session
            response_message = "Sakhani Chigawo Chomwe mumakhala:\n"
            for idx, region in enumerate(regions, 1):
                response_message += f"{idx}. {region}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        else:
            # Log API error and show to the user
            print(f"Error fetching regions. Status Code: {api_response.status_code}, Response: {api_response.text}")
            return generate_response_xml("Error fetching region data. Please try again.", 2)
    except requests.exceptions.RequestException as e:
        # Log the request exception error and notify user
        print(f"RequestException occurred: {e}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def fetch_districts_and_respond(region, session):
    """Calls the Laravel API to get districts for a region and responds to the user."""
    laravel_url = f"https://chiweto.ch/insurance/api/districts?region={region}"
    try:
        api_response = requests.get(laravel_url)
        if api_response.status_code == 200:
            districts = api_response.json()
            session['districts'] = districts  # Store districts in session
            response_message = "Sakhani Boma lomwe mukukhala:\n"
            for idx, district in enumerate(districts, 1):
                response_message += f"{idx}. {district}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        else:
            return generate_response_xml("Error fetching district data. Please try again.", 2)
    except requests.exceptions.RequestException:
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def fetch_epas_and_respond(district, session):
    """Calls the Laravel API to get EPAs for a district and responds to the user."""
    laravel_url = f"https://chiweto.ch/insurance/api/epas?district={district}"
    try:
        api_response = requests.get(laravel_url)
        if api_response.status_code == 200:
            epas = api_response.json()
            session['epas'] = epas  # Store EPAs in session
            session['current_page'] = session.get('current_page', 1)  # Initialize current page
            page_size = 10  # Set page size
            total_pages = (len(epas) + page_size - 1) // page_size  # Calculate total pages
            session['total_pages'] = total_pages  # Store total pages in session

            # Get the current page EPAs to display
            start_index = (session['current_page'] - 1) * page_size
            end_index = start_index + page_size
            current_epas = epas[start_index:end_index]

            response_message = "Sakhani dera lanu lazaulimi (EPA):\n"
            for idx, epa in enumerate(current_epas, start=start_index + 1):
                response_message += f"{idx}. {epa}\n"

            # Add pagination information
            response_message += f"\nPage {session['current_page']} of {total_pages}.\n"
            if session['current_page'] > 1:
                response_message += "P. Kumbuyo\n"  # Option to go to the previous page
            if session['current_page'] < total_pages:
                response_message += "N. Kutsogolo\n"  # Option to go to the next page
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"

            return generate_response_xml(response_message, 2)
        else:
            return generate_response_xml("Error fetching EPA data. Please try again.", 2)
    except requests.exceptions.RequestException:
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def handle_epa_navigation(msg, session):
    """Handles navigation for EPA pagination."""
    if msg == 'N':  # Next page
        session['current_page'] += 1
    elif msg == 'P':  # Previous page
        session['current_page'] = max(1, session['current_page'] - 1)

    return fetch_epas_and_respond(session['selected_district'], session)

def fetch_livestocks_and_respond(session):
    """Calls the Laravel API to get livestock and responds to the user."""
    laravel_url = "https://chiweto.ch/insurance/api/livestock/get_all"
    
    try:
        api_response = requests.get(laravel_url)

        if api_response.status_code == 200:
            livestock_data = api_response.json()  # Get the full response including id and description
            if not livestock_data:
                return generate_response_xml("No livestock data available.", 2)

            session['livestock_data'] = livestock_data  # Store full livestock data in session

            # Only store the descriptions to display to the user
            livestock_list = [livestock['description'] for livestock in livestock_data]
            session['livestock'] = livestock_list  # Store descriptions in session for selection

            # Generate response message with list of livestock
            response_message = "Sankhani ziweto:\n"
            for idx, livestock_item in enumerate(livestock_list, 1):
                response_message += f"{idx}. {livestock_item}\n"
            response_message += "0. Tulukani"
            return generate_response_xml(response_message, 2)

        else:
            print(f"Error fetching livestock data: {api_response.text}")
            return generate_response_xml("Error fetching livestock data. Please try again.", 2)

    except requests.exceptions.RequestException as e:
        print(f"RequestException: {e}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def fetch_insurance_types_and_respond(session):
    """Fetch insurance types and respond to the user with options."""
    laravel_url = "https://chiweto.ch/insurance/api/livestock/get_insurance_type"
    
    try:
        # Make the API request to fetch insurance types
        api_response = requests.get(laravel_url)
        print(f"API Response Status Code: {api_response.status_code}")  # Log status code
        print(f"API Response Text: {api_response.text}")  # Log response text

        if api_response.status_code == 200:
            insurance_types_data = api_response.json()  # Get the full response with id and name
            if not insurance_types_data:
                return generate_response_xml("No insurance types available.", 2)

            session['insurance_data'] = insurance_types_data  # Store full insurance data (id and name) in session

            # Only store the names for displaying to the user
            insurance_list = [insurance['description'] for insurance in insurance_types_data]
            session['insurance'] = insurance_list  # Store the list of insurance names in session

            # Generate response message with insurance types for the user to select
            response_message = "Sankhani mtundu wa inshulasi:\n"
            for idx, insurance_type in enumerate(insurance_list, 1):
                response_message += f"{idx}. {insurance_type}\n"
            response_message += "0. Tulukani"
            return generate_response_xml(response_message, 2)

        else:
            print(f"API Error: {api_response.status_code} - {api_response.text}")
            return generate_response_xml(f"Error fetching insurance types. Status Code: {api_response.status_code}. Please try again.", 2)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching insurance types: {e}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)


def submit_insurance_data(session, msisdn):
    """Submit selected livestock, insurance, and phone number to the database."""
    livestock_type = session.get('selected_livestock_id','')
    insurance_type = session.get('selected_insurance_id','')

    if not livestock_type or not insurance_type or not msisdn:
        return generate_response_xml("Missing data. Please try again.", 2)

    laravel_url = "https://chiweto.ch/insurance/api/proposal/add_ussd"

    data = {
        'phone_number': msisdn,
        'phone': msisdn,
        'insurance_type': insurance_type,
        'livestock_type': livestock_type,
    }
    headers = {'Content-Type': 'application/json'}
    try:
        api_response = requests.post(laravel_url, json=data, headers=headers,verify=False)

        if api_response.status_code == 200:
            return generate_response_xml("Kulembetsa inshulasi kwatheka.", 2)
        else:
            message = f"Error submitting proposal.Please try again.\nPhone Number: {msisdn}\nLivestock Type: {livestock_type}\nInsurance Type: {insurance_type}"
            return generate_response_xml(message, 2)

    except requests.exceptions.RequestException as e:
        print(f"RequestException: {e}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)
    
def fetch_policies_and_respond(msisdn):
    """Call API to fetch policies for a registered user and respond with the options."""
    laravel_url = f"https://chiweto.ch/insurance/api/policies?msisdn={msisdn}"
    try:
        api_response = requests.get(laravel_url)
        if api_response.status_code == 200:
            policies = api_response.json()
            response_message = "Ma polise omwe muli nawo ndi awa:\n"
            for idx, policy in enumerate(policies, 1):
                response_message += f"{idx}. {policy['policy_name']} - {policy['status']}\n"
            response_message += "0. Bwelerani\n00. Koyambilira"
            return generate_response_xml(response_message, 2)
        else:
            response_message += "0. Bwelerani\n00. Koyambilira"
            return generate_response_xml("Palibe polise zomwe zilipo.Bwererani kumbuyo kut mugule polise.", 2)
    except requests.exceptions.RequestException:
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def submit_farmer_registration(session, msisdn):
    """Final submission to the Laravel API."""
    farmer_name = session.get('farmer_name', '')
    farmer_gender = session.get('farmer_gender', '')
    farmer_region = session.get('selected_region', '')
    farmer_district = session.get('selected_district', '')
    farmer_epa = session.get('selected_epa', '')

    data = {
        'phone': msisdn,
        'name': farmer_name,
        'gender': farmer_gender,
        'region': farmer_region,
        'district': farmer_district,
        'epa': farmer_epa
    }

    try:
        laravel_url = "https://chiweto.ch/insurance/api/register_client_ussd"
        api_response = requests.post(laravel_url, json=data, verify=False)
        if api_response.status_code == 200:
            return generate_response_xml("kutsekula akaunti kwatheka. Zikomo!", 3)  # End of session
        else:
            return generate_response_xml(f"Kulembesa akaunti kwakanika: {api_response.status_code}. Yeselaninso.", 2)
    except requests.exceptions.RequestException:
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)