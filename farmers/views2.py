import xml.etree.ElementTree as ET
import requests
import json
import traceback
from requests.exceptions import RequestException, Timeout, SSLError, HTTPError
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
import uuid
from datetime import datetime

# Payment Configuration
API_BASE_URL = "https://chiweto.ch/insurance/api"
API_KEY = "eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJqdGkiOiIwMzIzNWFmOS1lODA0LTQwNTMtYTI0Yy03Y2NmZDkxODE4YTIiLCJzdWIiOiIzMTciLCJpYXQiOjE2OTYzMjg2OTMsImV4cCI6MjAxMTk0Nzg5MywicG0iOiJEQUYsUEFGIiwidHQiOiJBQVQifQ.lXGqVT0Z2jew1nqP6jrv_Y8s7XUCDCclJS3wzRBtSbg" 

# Add a mapping for previous steps at the top of the file
PREVIOUS_STEP_MAP = {
    2: 1,  # Gender -> Name
    3: 2,  # Region -> Gender
    4: 3,  # District -> Region
    5: 4,  # EPA -> District
    'buy_insurance_select': 'buy_insurance',
    'buy_select_payment_method': 'buy_insurance_select',
    'buy_confirm_payment': 'buy_select_payment_method',
    'buy_process_payment': 'buy_confirm_payment',
    'view_approved_policies': 'policy_status_menu',
    'view_policy_details': 'view_approved_policies',
    'select_payment_method': 'view_policy_details',
    'confirm_policy_payment': 'select_payment_method',
    'process_policy_payment': 'confirm_policy_payment',
    'view_paid_policies': 'policy_status_menu',
    'policy_status_menu': 'registered_menu',
    'registered_menu': None,  # Home
    'buy_insurance': 'registered_menu',
}

def normalize_msisdn(msisdn):
    """Ensure MSISDN is in +265XXXXXXXXX format for Malawi."""
    msisdn = msisdn.strip()
    if msisdn.startswith('+265') and len(msisdn) == 13 and msisdn[1:].isdigit():
        return msisdn
    elif msisdn.startswith('0') and len(msisdn) == 10 and msisdn.isdigit():
        return '+265' + msisdn[1:]
    elif msisdn.startswith('265') and len(msisdn) == 12 and msisdn.isdigit():
        return '+' + msisdn
    else:
        return None

def call_advisor_flow(session, msisdn, msg):
    """Handles the Itanani Mlangizi (Call Advisor) flow."""
    # Normalize MSISDN
    normalized_msisdn = normalize_msisdn(msisdn)
    if not normalized_msisdn:
        return generate_response_xml("Nambala ya foni yolakwika. Yesaninso.", 2)
    
    try:
        # If vet list not displayed yet, fetch vets from API
        if 'vet_list_displayed' not in session:
            # Call the PHP API to get vets for this farmer
            api_url = "https://your-api-domain.com/api/initiate-vet-call"  # Replace with your actual API URL
            payload = {
                'phone_number': normalized_msisdn
            }
            
            response = requests.post(api_url, json=payload, timeout=10)
            response.raise_for_status()
            api_data = response.json()
            
            if response.status_code == 200 and 'vets' in api_data:
                vets = api_data['vets']
                
                if not vets:
                    return generate_response_xml(
                        "Palibe mlangizi wapezeka pa chipatala chanu. Chonde lemberani chipatala chanu.",
                        2
                    )
                
                # Store vets in session for selection
                session['available_vets'] = vets
                session['vet_list_displayed'] = True
                session.modified = True
                
                # Display vet list
                response_msg = "Sankhani mlangizi:\n"
                for idx, vet in enumerate(vets, 1):
                    vet_name = vet.get('name', vet.get('username', f'Mlangizi {idx}'))
                    response_msg += f"{idx}. {vet_name}\n"
                response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
                
                return generate_response_xml(response_msg, 2)
            else:
                error_msg = api_data.get('error', 'Palibe mlangizi wapezeka.')
                return generate_response_xml(f"{error_msg}\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        
        # Handle vet selection
        if msg == '0':  # Back
            session.pop('vet_list_displayed', None)
            session.pop('available_vets', None)
            session['current_step'] = 'registered_menu'
            session.modified = True
            return handle_registered_user_menu(session)
        elif msg == '00':  # Home
            session.pop('vet_list_displayed', None)
            session.pop('available_vets', None)
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
        elif msg.isdigit() and 1 <= int(msg) <= len(session.get('available_vets', [])):
            # Get selected vet
            selected_vet = session['available_vets'][int(msg) - 1]
            vet_username = selected_vet.get('username')
            
            if not vet_username:
                return generate_response_xml(
                    "Nambala ya mlangizi silipo. Sankhani mlangizi wina.",
                    2
                )
            
            # Clean up session
            session.pop('vet_list_displayed', None)
            session.pop('available_vets', None)
            session.modified = True
            
            # Return vet's username/phone for call initiation
            # You might need to adjust this based on how your system handles calls
            return generate_response_xml(vet_username, 3)
        else:
            return generate_response_xml(
                "Chisankho chosayenera. Sankhani mlangizi woyenera.",
                2
            )
            
    except Exception as e:
        print(f"Error in call_advisor_flow: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

@csrf_exempt
def handle_ussd(request):
    """Main USSD handler for processing incoming requests."""
    try:
        # Parse the incoming XML
        tree = ET.ElementTree(ET.fromstring(request.body))
        root = tree.getroot()

        msisdn = root.find('msisdn').text
        session_id = root.find('sessionid').text
        request_type = int(root.find('type').text)
        msg = root.find('msg').text

        # Use Django session
        session = request.session
        if request_type == 1:  # New session
            session.clear()  # Reset session
            session['msisdn'] = msisdn
            session['session_id'] = session_id
            session.modified = True

        current_step = session.get('current_step', 1)
        print(f"USSD Request: msisdn={msisdn}, session_id={session_id}, request_type={request_type}, msg={msg}, current_step={current_step}")

        # Handle navigation: back or home
        if msg == '0':  # Back to previous step
            prev_step = PREVIOUS_STEP_MAP.get(current_step, 1)
            session['current_step'] = prev_step
            session.modified = True
            # If previous step is None, go to home
            if prev_step is None:
                return handle_restart()
            return handle_back_step(prev_step, session)
        if msg == '00':  # Return to home
            # Check if user is registered
            if check_if_user_registered(msisdn):
                session['current_step'] = 'registered_menu'
                session.modified = True
                return handle_registered_user_menu(session)
            else:
                session['current_step'] = 1
                session.modified = True
                return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):", 2)

        # New session
        if request_type == 1:
            user_registered = check_if_user_registered(msisdn)
            if user_registered:
                session['current_step'] = 'request_pin'
                session.modified = True
                return generate_response_xml("Mwalandiridwa ku Chiweto.\nLowetsani nambala yachinsisi:", 2)
            else:
                session['current_step'] = 1
                session.modified = True
                return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):", 2)

        # Handle existing session
        if request_type == 2:
            if current_step == 'request_pin':
                pin = msg
                print(f"Validating PIN for msisdn={msisdn}, pin={pin}")
                if validate_pin(msisdn, pin, session):
                    return handle_registered_user_menu(session)
                return generate_response_xml(f"Nambala yachinsisi yolakwika: {msisdn}. Yesaninso:", 2)

            elif current_step == 1:  # Name input
                session['farmer_name'] = msg.strip()
                session['current_step'] = 2
                session.modified = True
                return generate_response_xml("Ndinu mamuna kapena mkazi:\n1. Mamuna\n2. Mkazi\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

            elif current_step == 2:  # Gender selection
                if msg == '1':
                    session['farmer_gender'] = 'M'
                elif msg == '2':
                    session['farmer_gender'] = 'F'
                else:
                    return generate_response_xml("Mwasankha njira yolakwika:\n1. Mamuna\n2. Mkazi\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
                session['current_step'] = 3
                session.modified = True
                return fetch_regions_and_respond(session)

            elif current_step == 3:  # Region selection
                regions = session.get('regions', [])
                if not msg.isdigit() or int(msg) < 1 or int(msg) > len(regions):
                    return generate_response_xml("Mwasankha chigawo cholakwika. Yesaninso.", 2)
                session['selected_region'] = regions[int(msg) - 1]
                session['current_step'] = 4
                session.modified = True
                return fetch_districts_and_respond(session['selected_region'], session)

            elif current_step == 4:  # District selection with pagination
                region = session.get('selected_region', '')
                if not region:
                    print("No region found in session for district selection")
                    return generate_response_xml("Chigawo sichinatsimikizike. Yesaninso.", 2)
                return handle_district_navigation(msg, session, region, msisdn)

            elif current_step == 5:  # EPA selection with pagination
                district = session.get('selected_district', '')
                if not district:
                    print("No district found in session for EPA selection")
                    return generate_response_xml("Boma silinatsimikizike. Yesaninso.", 2)
                return handle_epa_navigation(msg, session, district, msisdn)

            elif current_step == 'registered_menu':
                if msg == '1':
                    session['current_step'] = 'buy_insurance'
                    session.modified = True
                    return fetch_livestocks_and_respond(session)
                elif msg == '2':
                    session['current_step'] = 'call_advisor'
                    session.modified = True
                    return generate_response_xml("Lowetsani nambala ya mlangizi:", 2)
                elif msg == '3':
                    session['current_step'] = 'policy_status_menu'
                    session.modified = True
                    return generate_response_xml(
                        "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
                        2
                    )
                elif msg == '4':
                    session['current_step'] = 'end'
                    session.modified = True
                    return generate_response_xml("Zikomo pogwiritsa ntchito Chiweto. Tsalani bwino.", 3)
                return generate_response_xml(
                    "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Bwelerani Koyambilira",
                    2
                )

            elif current_step == 'policy_status_menu':
                if msg == '1':  # Approved policies (Ololedwa)
                    session['current_step'] = 'view_approved_policies'
                    session.modified = True
                    return fetch_approved_policies(msisdn, session)
                elif msg == '2':  # Rejected policies (Okanidwa)
                    return handle_policy_status(session, msg, msisdn, status=2)
                elif msg == '3':  # Pending policies
                    return handle_policy_status(session, msg, msisdn, status=3)
                elif msg == '4':  # Paid policies (Olipilidwa)
                    session['current_step'] = 'view_paid_policies'
                    session.modified = True
                    return fetch_paid_policies(msisdn, session)
                elif msg == '0':
                    return handle_registered_user_menu(session)
                elif msg == '00':
                    return handle_restart()
                return generate_response_xml(
                    "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )

            elif current_step == 'view_policy_details':
                # Only allow 98 (Lipira Polise), 0 (Back), 00 (Home)
                if msg == '98':
                    # Proceed to payment method selection and fetch payment options
                    session['current_step'] = 'select_payment_method'
                    session.modified = True
                    return fetch_payment_options(session)
                elif msg == '0':
                    session['current_step'] = 'view_approved_policies'
                    session.modified = True
                    return fetch_approved_policies(msisdn, session)
                elif msg == '00':
                    session['current_step'] = 1
                    session.modified = True
                    return handle_restart()
                else:
                    return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)
                
            elif current_step == 'view_approved_policies':
                return handle_approved_policy_selection(msg, session, msisdn)
                
            elif current_step == 'select_payment_method':
                return handle_payment_method_selection(msg, session, msisdn)
                
            elif current_step == 'confirm_policy_payment':
                return handle_payment_confirmation(msg, session, msisdn)
                
            elif current_step == 'process_policy_payment':
                return process_policy_payment(msg, session, msisdn)
                
            elif current_step == 'view_paid_policies':
                return display_paid_policies(msg, session, msisdn)

            elif current_step == 'buy_insurance':
                if msg == '0':
                    session['current_step'] = 'registered_menu'
                    session.modified = True
                    return handle_registered_user_menu(session)
                elif msg == '00':
                    session['current_step'] = 1
                    session.modified = True
                    return handle_restart()
                elif msg.isdigit() and 1 <= int(msg) <= len(session.get('livestock', [])):
                    selected_livestock_index = int(msg) - 1
                    selected_livestock = session['livestock_data'][selected_livestock_index]
                    session['selected_livestock_id'] = selected_livestock['id']
                    session['selected_livestock'] = selected_livestock['description']
                    session['current_step'] = 'buy_insurance_select'
                    session.modified = True
                    return fetch_insurance_types_and_respond(session)
                livestock_list = session.get('livestock', [])
                response_message = "Sankhani ziweto:\n"
                for idx, livestock in enumerate(livestock_list, 1):
                    response_message += f"{idx}. {livestock}\n"
                response_message += "0. Bwelerani\n00.Bwelerani Koyambilira"
                return generate_response_xml(response_message, 2)

            elif current_step == 'buy_insurance_select':
                if msg == '0':
                    session['current_step'] = 'registered_menu'
                    session.modified = True
                    return handle_registered_user_menu(session)
                elif msg == '00':
                    session['current_step'] = 1
                    session.modified = True
                    return handle_restart()
                elif msg.isdigit() and 1 <= int(msg) <= len(session.get('insurance', [])):
                    selected_insurance_index = int(msg) - 1
                    selected_insurance = session['insurance_data'][selected_insurance_index]
                    session['selected_insurance_id'] = selected_insurance['id']
                    session['selected_insurance'] = selected_insurance['description']
                    session.modified = True
                    return submit_insurance_data(session, msisdn)
                insurance_list = session.get('insurance', [])
                response_message = "Sankhani mtundu wa inshulansi:\n"
                for idx, insurance in enumerate(insurance_list, 1):
                    response_message += f"{idx}. {insurance}\n"
                response_message += "0. Bwelerani\n00.Bwelerani Koyambilira"
                return generate_response_xml(response_message, 2)

            elif current_step == 'buy_select_payment_method':
                return handle_buy_payment_method_selection(msg, session, msisdn)

            elif current_step == 'buy_confirm_payment':
                return handle_buy_payment_confirmation(msg, session, msisdn)

            elif current_step == 'buy_process_payment':
                return process_buy_policy_payment(msg, session, msisdn)

            elif current_step == 'call_advisor':
                return call_advisor_flow(session, msisdn, msg)

        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

    except Exception as e:
        print(f"USSD Handler Error: {str(e)} - {type(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

# ====== POLICY STATUS FUNCTIONS ======

def handle_policy_status(session, user_input, msisdn, status):
    """Handles viewing policies by status (approved, rejected, paid)"""
    try:
        status_labels = {
            1: "Operekedwa",
            2: "Ololedwa",
            3: "Okanidwa", 
            4: "Olipidwa"
        }
        
        if user_input == '0':  # Back
            session['current_step'] = 'policy_status_menu'
            session.modified = True
            return generate_response_xml(
                "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
            
        # Fetch policies for the given status
        url = f"{API_BASE_URL}/proposals/all?username={msisdn}&status={status-1}"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            policies = response.json().get('data', [])
            if not policies:
                return generate_response_xml(f"Palibe ma polise {status_labels.get(status, '')} omwe alipo.", 2)
                
            session[f'policies_status_{status}'] = policies
            session['current_policy_page'] = 1
            session.modified = True
            
            response_msg = f"Ma Polise {status_labels.get(status, '')}:\n"
            for idx, policy in enumerate(policies[:5], 1):
                response_msg += f"{idx}. {policy.get('insurance_type')} - {policy.get('livestock_type', '')}\n"
            
            if status == 1:  # Only show payment option for approved policies
                response_msg += "98. Lipirani Polise\n" if len(policies) > 0 else ""
            
            response_msg += "0. Bweleran\n00. Bwelerani Koyambilira"
            
            return generate_response_xml(response_msg, 2)
            
        return generate_response_xml(f"Zolakwika pakutenga ma polise {status_labels.get(status, '')}. Yesaninso.", 2)
        
    except Exception as e:
        print(f"Error handling policy status {status}: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

def fetch_approved_policies(msisdn, session):
    """Fetches approved policies from the API with retry logic, using 'value' field"""
    try:
        # Normalize MSISDN
        msisdn = msisdn if msisdn.startswith('+') else f"+{msisdn}"
        if not msisdn.startswith('+265') or not msisdn[1:].isdigit() or len(msisdn) != 13:
            print(f"Error: Invalid MSISDN format: {msisdn}")
            return generate_response_xml(
                "Nambala yafoni yolakwika. Chonde lowetsani nambala yanu ya +265.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        
        url = f"{API_BASE_URL}/proposals/all?username={msisdn}&status=1"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json"
        }
        print(f"Fetching approved policies for MSISDN: {msisdn}, URL: {url}, Headers: {headers}")
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            try:
                api_response = response.json()
                if not api_response.get('success', False):
                    print(f"Error: API returned success=false, response: {api_response}")
                    return generate_response_xml(
                        "Zolakwika pakutenga ma polise: Server response invalid. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                        2
                    )
                policies = api_response.get('data', [])
                print(f"API Response for approved policies: {policies}")
                # Normalize and filter policies
                normalized_policies = [
                    {
                        'insurance_type': p.get('insurance_type', 'Unknown Insurance'),
                        'value': int(p.get('value', '0')),  # Convert string to int
                        'livestock_type': p.get('livestock_type', 'Unknown'),
                        'livestock_quantity': p.get('livestock_quantity', 1),
                        'duration': p.get('duration', 12),
                        'policy_number': p.get('policy_number', str(p.get('id', '')))
                    } for p in policies
                    if p.get('insurance_type') and p.get('value', '0').isdigit() and int(p.get('value', '0')) > 0
                ]
                if not normalized_policies:
                    print("Error: No valid policies found")
                    return generate_response_xml(
                        "Palibe ma polise ololedwa omwe alipo kwa nambala iyi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                        2
                    )
                session['approved_policies'] = normalized_policies
                session['current_policy_page'] = 1
                session.modified = True
                response_msg = "Ma Polise Ololedwa:\n"
                for idx, policy in enumerate(normalized_policies[:5], 1):
                    response_msg += f"{idx}. {policy.get('insurance_type')} - MK{policy.get('value')}\n"
                # response_msg += "98. Lipirani Polise\n" if len(normalized_policies) > 0 else ""
                response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
                print(f"Normalized policies stored in session: {normalized_policies}")
                return generate_response_xml(response_msg, 2)
            except ValueError as e:
                print(f"Error parsing API response: {str(e)}, body={response.text}")
                return generate_response_xml(
                    "Zolakwika pakutenga ma polise: Invalid server response. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
        
        print(f"API request failed: status={response.status_code}, body={response.text}")
        if response.status_code == 503:
            return generate_response_xml(
                "Service ikukonzedwa. Chonde funsani othandizira pa support@chiweto.ch kapena yesaninso pambuyo pakanthawi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif response.status_code in [401, 403]:
            print("Error: Authentication failure with API key")
            return generate_response_xml(
                "Zolakwika pakuvomerezeka kwa API. Chonde funsani othandizira pa support@chiweto.ch.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif response.status_code == 400:
            print(f"Error: Invalid MSISDN or request parameters: {msisdn}")
            return generate_response_xml(
                "Zolakwika pa nambala yafoni. Chonde yang'anani nambala yanu.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif response.status_code == 404:
            print(f"Error: No approved policies found for MSISDN: {msisdn}")
            return generate_response_xml(
                "Palibe ma polise ololedwa omwe alipo kwa nambala iyi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        return generate_response_xml(
            f"Zolakwika pakutenga ma polise: Error {response.status_code}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )
    except requests.exceptions.Timeout as e:
        print(f"Error fetching approved policies: Timeout after 20s, {str(e)}")
        return generate_response_xml(
            "Palibe intaneti kapena service ikukonzedwa. Yesaninso pambuyo pakanthawi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )
    except requests.exceptions.ConnectionError as e:
        print(f"Error fetching approved policies: Connection error, {str(e)}")
        return generate_response_xml(
            "Palibe intaneti. Chonde yang'anani intaneti yanu ndi yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )
    except requests.exceptions.HTTPError as e:
        print(f"Error fetching approved policies: HTTP error, {str(e)}")
        return generate_response_xml(
            f"Zolakwika pakutenga ma polise: HTTP error {e.response.status_code}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )
    except Exception as e:
        print(f"Error fetching approved policies: Unexpected error, {str(e)}")
        return generate_response_xml(
            "Zolakwika zidachitika pakutenga ma polise. Yesaninso pambuyo pakanthawi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )

def handle_approved_policy_selection(user_input, session, msisdn):
    """Handles selection of approved policies with payment trigger on '98'"""
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        # Debug session state
        print(f"Debug - Session ID: {session.session_key}")
        print(f"Debug - Current Step: {current_step}")
        print(f"Debug - Session Keys: {list(session.keys())}")

        # Get approved policies
        policies = session.get('approved_policies', [])
        print(f"Debug - Policies in session: {policies}")
        
        # Refresh if no policies
        if not policies:
            print("Warning: No policies in session, refreshing...")
            session['force_refresh_policies'] = True
            session.modified = True
            return fetch_approved_policies(msisdn, session)
            
        # Navigation options
        if user_input == '0':  # Back
            prev_step = PREVIOUS_STEP_MAP.get(current_step, 1)
            session['current_step'] = prev_step
            session.modified = True
            # If previous step is None, go to home
            if prev_step is None:
                return handle_restart()
            return handle_back_step(prev_step, session)
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
            
        # Payment trigger (changed to '98')
        elif user_input == '98':
            selected_policy = session.get('selected_policy')
            print(f"Debug - Selected Policy for Payment: {selected_policy}")
            
            if not selected_policy:
                print("Error: Payment attempted without selected policy")
                # Re-prompt policy selection
                response_msg = "Sankhani polise kaye kuti mulipire:\n"
                for idx, policy in enumerate(policies[:5], 1):
                    response_msg += f"{idx}. {policy.get('insurance_type')} - MK{policy.get('value')}\n"
                response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
                return generate_response_xml(response_msg, 2)
            
            # Policy validation
            required_fields = ['insurance_type', 'value', 'livestock_type']
            missing_fields = [field for field in required_fields if field not in selected_policy]
            
            if missing_fields:
                print(f"Error: Missing fields in policy: {missing_fields}")
                return generate_response_xml(
                    f"Polise yosankhidwa ilibe {', '.join(missing_fields)}. Sankhani ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
                
            if not isinstance(selected_policy['value'], (int, float)) or selected_policy['value'] <= 0:
                print(f"Error: Invalid policy amount: {selected_policy['value']}")
                return generate_response_xml(
                    "Mtengo wa polise ndi wolakwika. Sankhani ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
            # Proceed to payment method selection and fetch payment options
            print("Proceeding to payment method selection and fetching payment options")
            session['current_step'] = 'select_payment_method'
            session.modified = True
            return fetch_payment_options(session)
            
        # Policy selection (options 1-5)
        elif user_input.isdigit() and 1 <= int(user_input) <= len(policies):
            selected_idx = int(user_input) - 1
            selected_policy = policies[selected_idx]
            print(f"Debug - Selected Policy Index: {selected_idx}, Policy: {selected_policy}")
            
            # Validate policy
            required_fields = ['insurance_type', 'value', 'livestock_type']
            missing_fields = [field for field in required_fields if field not in selected_policy]
            
            if missing_fields:
                print(f"Error: Policy missing fields: {missing_fields}")
                return generate_response_xml(
                    f"Polise yosankhidwa ilibe {', '.join(missing_fields)}. Sankhani ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
                
            if not isinstance(selected_policy['value'], (int, float)) or selected_policy['value'] <= 0:
                print(f"Error: Invalid policy amount: {selected_policy['value']}")
                return generate_response_xml(
                    "Mtengo wa polise ndi wolakwika. Sankhani ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
            
            # Store selected policy and show details
            session['selected_policy'] = selected_policy
            session['current_step'] = 'view_policy_details'
            session.modified = True
            
            response_msg = (
                f"Polise Ya:\n{selected_policy['insurance_type']}\n"
                f"Mtengo: MK{selected_policy['value']}\n\n"
                f"98. Lipirani Polise\n"  # Changed to '98' to match payment trigger
                f"0. Bwelerani\n"
                f"00. Bwelerani Koyambilira"
            )
            return generate_response_xml(response_msg, 2)
            
        # Invalid input fallback
        print(f"Warning: Invalid user input: {user_input}")
        return generate_response_xml(
            "Chisankho chosavomerezeka. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )
        
    except Exception as e:
        print(f"Critical Error: {str(e)}\n{traceback.format_exc()}")
        return generate_response_xml(
            "Zolakwika zakukulu. Chonde yesaninso pambuyo pake.\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )

def fetch_paid_policies(msisdn, session):
    """Fetches already paid policies (option 4 in status menu)"""
    try:
        url = f"{API_BASE_URL}/proposals/all?username={msisdn}&status=3"  # Status 3 = paid
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            policies = response.json().get('data', [])
            if not policies:
                return generate_response_xml("Palibe ma polise olipidwa omwe alipo.", 2)
                
            session['paid_policies'] = policies
            session['current_policy_page'] = 1
            session.modified = True
            
            response_msg = "Ma Polise Olipidwa:\n"
            for idx, policy in enumerate(policies[:5], 1):
                response_msg += f"{idx}. {policy.get('insurance_type')} - MK{policy.get('value', 0)}\n"
            
            response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
            
            return generate_response_xml(response_msg, 2)
            
        return generate_response_xml("Zolakwika pakutenga ma polise olipidwa. Yesaninso.", 2)
        
    except Exception as e:
        print(f"Error fetching paid policies: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try later.", 2)

def display_paid_policies(user_input, session, msisdn):
    """Displays paid policies (read-only view)"""
    try:
        if user_input == '0':  # Back
            session['current_step'] = 'policy_status_menu'
            session.modified = True
            return generate_response_xml(
                "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00.Bwelerani Koyambilira",
                2
            )
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
            
        return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)
        
    except Exception as e:
        print(f"Error displaying paid policies: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

# ====== PAYMENT FUNCTIONS ======

def fetch_payment_options(session):
    """Fetches available payment methods from GetPaymentOptions endpoint"""
    try:
        url = f"{API_BASE_URL}/payments/options"
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            payment_methods = response.json()
            if not payment_methods:
                return generate_response_xml("Palibe njira zolipira zomwe zilipo.", 2)
                
            session['payment_methods'] = payment_methods
            session.modified = True
            
            response_msg = "Sankhani njira yolipira:\n"
            for idx, method in enumerate(payment_methods, 1):
                response_msg += f"{idx}. {method}\n"
            response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
            
            return generate_response_xml(response_msg, 2)
            
        return generate_response_xml("Zolakwika pakutenga njira zolipira. Yesaninso.", 2)
        
    except Exception as e:
        print(f"Error fetching payment methods: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try later.", 2)

def handle_payment_method_selection(user_input, session, msisdn):
    """Handles payment method selection with proper MNO status checking"""
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        payment_methods = session.get('payment_methods', [])
        print(f"handle_payment_method_selection: payment_methods={payment_methods}, user_input={user_input}")
        
        # Validate selected_policy
        policy = session.get('selected_policy', {})
        if not policy:
            print("Error: No selected policy in session")
            return generate_response_xml(
                "Palibe polise yosankhidwa. Chonde sankhani polise kaye.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        
        required_fields = ['insurance_type', 'value', 'livestock_type']
        missing_fields = [field for field in required_fields if not policy.get(field)]
        if missing_fields:
            print(f"Error: Missing policy fields: {missing_fields}, policy={policy}")
            return generate_response_xml(
                f"Zolakwika pa polise: Missing {', '.join(missing_fields)}. Chonde sankhani polise yatsopano.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        if policy.get('value', 0) <= 0:
            print(f"Error: Invalid policy value: {policy.get('value')}, policy={policy}")
            return generate_response_xml(
                "Zolakwika pa polise: Mtengo wosayenera. Chonde sankhani polise yatsopano.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        
        if user_input == '0':  # Back
            prev_step = PREVIOUS_STEP_MAP.get(current_step, 1)
            session['current_step'] = prev_step
            session.modified = True
            # If previous step is None, go to home
            if prev_step is None:
                return handle_restart()
            return handle_back_step(prev_step, session)
            
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
            
        elif user_input.isdigit() and 1 <= int(user_input) <= len(payment_methods):
            selected_method = payment_methods[int(user_input) - 1]
            print(f"handle_payment_method_selection: Storing selected_payment_method={selected_method}")
            
            # Map payment methods to MNO codes
            mno_mapping = {
                'airtel money': 'AIRTEL_MWI',
                'tnm mpamba': 'TNM_MWI'
            }
            
            # Check MNO status only for mobile money options
            if selected_method.lower() in mno_mapping:
                mno_code = mno_mapping[selected_method.lower()]
                mno_status = check_mno_status(mno_code)
                print(f"MNO Status for {selected_method} ({mno_code}): {mno_status}")
                
                if mno_status.upper() != "OPERATIONAL":
                    # Find alternative payment methods
                    other_methods = [m for m in payment_methods 
                                   if m.lower() not in mno_mapping.keys()]
                    
                    error_msg = (
                        f"{selected_method} sipezeka pakadali pano. "
                        f"Status: {mno_status}\n\n"
                    )
                    
                    if other_methods:
                        error_msg += "Sankhani njira ina yolipira:\n"
                        for idx, method in enumerate(other_methods, 1):
                            error_msg += f"{idx}. {method}\n"
                        session['payment_methods'] = other_methods
                    else:
                        error_msg += "Palibe njira zina zolipira.\n"
                    
                    error_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
                    session.modified = True
                    return generate_response_xml(error_msg, 2)
            
            # If MNO is available or not a mobile money option
            session['selected_payment_method'] = selected_method
            session['current_step'] = 'confirm_policy_payment'
            session.modified = True
            
            return generate_response_xml(
                f"Malipiro a {policy.get('insurance_type')}\n"
                f"Njira: {selected_method}\n"
                f"Mtengo: MK{policy.get('value')}\n\n"
                f"1. Eya\n"
                f"2. Ayi\n"
                f"0. Bwelerani\n"
                f"00. Bwelerani Koyambilira",
                2
            )
                
        return generate_response_xml("Chisankho chosayenera. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        
    except Exception as e:
        print(f"Error handling payment method selection: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

def handle_payment_confirmation(user_input, session, msisdn):
    """Handles payment confirmation"""
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        if user_input == '1':  # Confirm payment
            policy = session.get('selected_policy', {})
            payment_method = session.get('selected_payment_method', '')
            print(f"handle_payment_confirmation: selected_policy={policy}, selected_payment_method={payment_method}")
            
            # Validate session data
            if not policy or not payment_method:
                print("Error: Missing policy or payment method in session")
                return generate_response_xml(
                    "Palibe polise kapena njira yolipira yosankhidwa. Chonde sankhani polise ndi njira yolipira kaye.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
            
            # Validate required policy fields
            required_fields = ['insurance_type', 'value', 'livestock_type']
            missing_fields = [field for field in required_fields if not policy.get(field)]
            if missing_fields:
                print(f"Error: Missing policy fields: {missing_fields}")
                return generate_response_xml(
                    f"Zolakwika pa data ya polise: Missing {', '.join(missing_fields)}. Chonde sankhani polise yatsopano.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
                
            # Validate value
            if policy.get('value', 0) <= 0:
                print(f"Error: Invalid policy value: {policy.get('value')}")
                return generate_response_xml(
                    "Zolakwika pa data ya polise: Mtengo wosayenera. Chonde sankhani polise yatsopano.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
                
            session['current_step'] = 'process_policy_payment'
            session.modified = True
            
            # Prepare payment data
            payment_data = {
                "phone_number": msisdn,
                "user": msisdn,
                "insurance_type": policy.get('insurance_type', ''),
                "livestock_type": policy.get('livestock_type', ''),
                "livestock_quantity": policy.get('livestock_quantity', 1),
                "payment_method": payment_method,
                "total_amount": policy.get('value', 0),
                "duration": policy.get('duration_in_possession', 12),
                "user_token": str(uuid.uuid4()),
                "proposal_id": policy.get('policy_number', policy.get('proposal_id', ''))
            }
            print(f"Payment data: {payment_data}")
            
            # Call PayFees endpoint
            url = f"{API_BASE_URL}/payments/pay"
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            response = requests.post(url, json=payment_data, headers=headers, timeout=15)
            print(f"PayPawaPay response: status={response.status_code}, body={response.text}")
            
            if response.status_code == 200:
                try:
                    payment_response = response.json()
                    print(f"Parsed payment response: {payment_response}")

                    # Handle numeric success codes (0, 1, 2, 3, etc.)
                    if isinstance(payment_response, (int, float)):
                        if payment_response in [0, 1, 2, 3]:  # Common success codes
                            return generate_response_xml("Malipiro anu atheka! Zikomo.", 3)
                        else:
                            return generate_response_xml(f"Zolakwika pa malipiro: Unexpected response ({payment_response}). Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

                    # Handle dictionary responses
                    if not isinstance(payment_response, dict):
                        return generate_response_xml(f"Zolakwika pa malipiro: Unexpected response format. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

                    if not payment_response.get('error'):
                        # Payment successful
                        return generate_response_xml("Malipiro anu atheka! Zikomo.", 3)
                    
                    elif payment_response.get('error_status') == "MNO_UNAVAILABLE":
                        return generate_response_xml(
                            f"Malipiro sanatheke: {payment_method} sipezeka ({payment_response.get('error_message')}). "
                            "Yesaninso pambuyo pakanthawi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    elif payment_response.get('error_status') in ["REJECTED", "DUPLICATE_IGNORED"]:
                        return generate_response_xml(
                            f"Malipiro sanatheke: {payment_response.get('error_message')}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    elif "E150" in payment_response.get('error_message', ''):
                        return generate_response_xml(
                            f"Malipiro sanatheke: Zolakwika za {payment_method} (E150). Chonde yesaninso kapena gwiritsani ntchito njira ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    else:
                        return generate_response_xml(
                            f"Zolakwika pa malipiro: {payment_response.get('error_message', 'Unknown error')}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                except ValueError as e:
                    print(f"Error parsing PayPawaPay response: {str(e)}, body={response.text}")
                    return generate_response_xml(
                        "Zolakwika pa malipiro: Invalid response from payment service. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                        2
                    )
                    
            error_msg = response.text[:100] if response.text else "Unknown error"
            if "E150" in error_msg:
                error_msg = f"Zolakwika za {payment_method} (E150). Chonde yesaninso kapena gwiritsani ntchito njira ina."
            print(f"PayPawaPay request failed: status={response.status_code}, error={error_msg}")
            return generate_response_xml(f"Zolakwika pa malipiro: {error_msg}\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
            
        elif user_input == '2':  # Cancel
            session['current_step'] = 'select_payment_method'
            session.modified = True
            return fetch_payment_options(session)
            
        elif user_input == '0':  # Back
            session['current_step'] = 'select_payment_method'
            session.modified = True
            return fetch_payment_options(session)
            
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
            
        return generate_response_xml("Chisankho chosayenera. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        
    except Exception as e:
        print(f"Error handling payment confirmation: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

def process_policy_payment(user_input, session, msisdn):
    """Processes payment for a selected policy"""
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        if user_input == '0':  # Back
            session['current_step'] = 'confirm_policy_payment'
            session.modified = True
            policy = session.get('selected_policy', {})
            payment_method = session.get('selected_payment_method', '')
            return generate_response_xml(
                f"Malipiro a {policy.get('insurance_type')}\n"
                f"Njira: {payment_method}\n"
                f"Mtengo: MK{policy.get('value', 0)}\n\n"
                f"1. Eya\n"
                f"2. Ayi\n"
                f"0. Bwelerani\n"
                f"00. Bwelerani Koyambilira",
                2
            )
        elif user_input == '00':  # Home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
        else:
            # Use the PIN from session if available
            pin = session.get('entered_payment_pin', '')
            policy = session.get('selected_policy', {})
            payment_method = session.get('selected_payment_method', '')
            # Normalize msisdn before payment
            normalized_msisdn = normalize_msisdn(msisdn)
            if not normalized_msisdn:
                return generate_response_xml(
                    "Nambala yafoni yolakwika. Lowetsani nambala yoyenera (e.g. 0888123456 or +265888123456).",
                    2
                )
            payment_data = {
                "phone_number": normalized_msisdn,
                "user": normalized_msisdn,
                "insurance_type": policy.get('insurance_type', ''),
                "livestock_type": policy.get('livestock_type', ''),
                "livestock_quantity": policy.get('livestock_quantity', 1),
                "payment_method": payment_method,
                "total_amount": policy.get('value', 0),
                "duration": policy.get('duration_in_possession', 12),
                "user_token": str(uuid.uuid4()),
                "pin": pin,
                "proposal_id": policy.get('policy_number', policy.get('proposal_id', ''))
            }
            print(f"Payment data: {payment_data}")
            url = f"{API_BASE_URL}/payments/pay"
            headers = {
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            response = requests.post(url, json=payment_data, headers=headers, timeout=15)
            print(f"PayPawaPay response: status={response.status_code}, body={response.text}")
            if response.status_code == 200:
                try:
                    payment_response = response.json()
                    print(f"Parsed payment response: {payment_response}")
                    
                    # Handle numeric success codes (0, 1, 2, 3, etc.)
                    if isinstance(payment_response, (int, float)):
                        if payment_response in [0, 1, 2, 3]:  # Common success codes
                            return generate_response_xml("Malipiro anu atheka! Zikomo.", 3)
                        else:
                            return generate_response_xml(f"Zolakwika pa malipiro: Unexpected response ({payment_response}). Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
                    
                    # Handle dictionary responses
                    if not isinstance(payment_response, dict):
                        return generate_response_xml(f"Zolakwika pa malipiro: Unexpected response format. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
                    
                    if not payment_response.get('error'):
                        return generate_response_xml("Malipiro anu atheka! Zikomo.", 3)
                    elif payment_response.get('error_status') == "MNO_UNAVAILABLE":
                        return generate_response_xml(
                            f"Malipiro sanatheke: {payment_method} sipezeka ({payment_response.get('error_message')}). "
                            "Yesaninso pambuyo pakanthawi.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    elif payment_response.get('error_status') in ["REJECTED", "DUPLICATE_IGNORED"]:
                        return generate_response_xml(
                            f"Malipiro sanatheke: {payment_response.get('error_message')}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    elif "E150" in payment_response.get('error_message', ''):
                        return generate_response_xml(
                            f"Malipiro sanatheke: Zolakwika za {payment_method} (E150). Chonde yesaninso kapena gwiritsani ntchito njira ina.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                    else:
                        return generate_response_xml(
                            f"Zolakwika pa malipiro: {payment_response.get('error_message', 'Unknown error')}. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                            2
                        )
                except ValueError as e:
                    print(f"Error parsing PayPawaPay response: {str(e)}, body={response.text}")
                    return generate_response_xml(
                        "Zolakwika pa malipiro: Invalid response from payment service. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                        2
                    )
            error_msg = response.text[:100] if response.text else "Unknown error"
            if "E150" in error_msg:
                error_msg = f"Zolakwika za {payment_method} (E150). Chonde yesaninso kapena gwiritsani ntchito njira ina."
            print(f"PayPawaPay request failed: status={response.status_code}, error={error_msg}")
            return generate_response_xml(f"Zolakwika pa malipiro: {error_msg}\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        # return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)    
    except Exception as e:
        print(f"Error processing policy payment: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

def check_mno_status(mno):
    print(f"Bypassing MNO status check for {mno} - assuming OPERATIONAL")
    return "OPERATIONAL"

def handle_buy_payment_method_selection(user_input, session, msisdn):
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        payment_methods = session.get('payment_methods', [])
        print(f"handle_buy_payment_method_selection: payment_methods={payment_methods}, user_input={user_input}")
        # Validate selected insurance
        insurance_id = session.get('selected_insurance_id')
        livestock_id = session.get('selected_livestock_id')
        if not insurance_id or not livestock_id:
            print("Error: No selected insurance or livestock in session")
            return generate_response_xml(
                "Palibe inshulansi kapena ziweto zosankhidwa. Chonde sankhani kaye.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        if user_input == '0':
            session['current_step'] = 'buy_insurance_select'
            session.modified = True
            return fetch_insurance_types_and_respond(session)
        elif user_input == '00':
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
        elif user_input.isdigit() and 1 <= int(user_input) <= len(payment_methods):
            selected_method = payment_methods[int(user_input) - 1]
            # MNO status check (reuse logic)
            mno_mapping = {
                'airtel money': 'AIRTEL_MWI',
                'tnm mpamba': 'TNM_MWI'
            }
            if selected_method.lower() in mno_mapping:
                mno_code = mno_mapping[selected_method.lower()]
                mno_status = check_mno_status(mno_code)
                if mno_status.upper() != "OPERATIONAL":
                    other_methods = [m for m in payment_methods if m.lower() not in mno_mapping.keys()]
                    error_msg = (
                        f"{selected_method} sipezeka pakadali pano. Status: {mno_status}\n\n"
                    )
                    if other_methods:
                        error_msg += "Sankhani njira ina yolipira:\n"
                        for idx, method in enumerate(other_methods, 1):
                            error_msg += f"{idx}. {method}\n"
                        session['payment_methods'] = other_methods
                    else:
                        error_msg += "Palibe njira zina zolipira.\n"
                    error_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"
                    session.modified = True
                    return generate_response_xml(error_msg, 2)
            session['selected_payment_method'] = selected_method
            session['current_step'] = 'buy_confirm_payment'
            session.modified = True
            insurance_desc = session.get('selected_insurance', '')
            livestock_desc = session.get('selected_livestock', '')
            # For demo, use a fixed price or fetch from session if available
            price = session.get('insurance_price', 1000)
            return generate_response_xml(
                f"Malipiro a {insurance_desc} pa {livestock_desc}\nNjira: {selected_method}\nMtengo: MK{price}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        return generate_response_xml("Chisankho chosayenera. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
    except Exception as e:
        print(f"Error handling buy payment method selection: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

def handle_buy_payment_confirmation(user_input, session, msisdn):
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        if user_input == '1':
            insurance_id = session.get('selected_insurance_id')
            livestock_id = session.get('selected_livestock_id')
            payment_method = session.get('selected_payment_method', '')
            if not insurance_id or not livestock_id or not payment_method:
                return generate_response_xml(
                    "Palibe inshulansi, ziweto kapena njira yolipira yosankhidwa. Chonde sankhani kaye.\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )
            session['current_step'] = 'buy_process_payment'
            session.modified = True
            return process_buy_policy_payment('1', session, msisdn)
        elif user_input == '2':
            session['current_step'] = 'buy_select_payment_method'
            session.modified = True
            return fetch_payment_options(session)
        elif user_input == '0':
            session['current_step'] = 'buy_select_payment_method'
            session.modified = True
            return fetch_payment_options(session)
        elif user_input == '00':
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
        return generate_response_xml("Chisankho chosayenera. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
    except Exception as e:
        print(f"Error handling buy payment confirmation: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)

def process_buy_policy_payment(user_input, session, msisdn):
    try:
        # Ensure current_step is defined
        current_step = session.get('current_step', 1)
        if user_input == '0':
            session['current_step'] = 'buy_confirm_payment'
            session.modified = True
            insurance_desc = session.get('selected_insurance', '')
            livestock_desc = session.get('selected_livestock', '')
            payment_method = session.get('selected_payment_method', '')
            price = session.get('value', 1000)
            return generate_response_xml(
                f"Malipiro a {insurance_desc} pa {livestock_desc}\nNjira: {payment_method}\nMtengo: MK{price}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif user_input == '00':
            session['current_step'] = 1
            session.modified = True
            return handle_restart()
        elif user_input == '1':
            # Simulate payment processing (call payment API if needed)
            # On success, call submit_insurance_data
            result = submit_insurance_data(session, msisdn)
            return result
        return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)
    except Exception as e:
        print(f"Error processing buy policy payment: {str(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

def check_if_user_registered(msisdn):
    """Checks if the user is registered via API."""
    registration_check_url = f"https://chiweto.ch/insurance/api/is_registered_ussd?msisdn={msisdn}"
    try:
        response = requests.get(registration_check_url, timeout=10)
        if response.status_code == 200:
            response_data = response.json()
            print(f"Registration check response: {response_data}")
            return response_data.get('is_registered', False)
        print(f"Registration check failed: status={response.status_code}, response={response.text}")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error checking registration status: {str(e)}")
        # Return False to allow new user registration when API is unavailable
        return False
    except requests.exceptions.Timeout as e:
        print(f"Timeout error checking registration status: {str(e)}")
        # Return False to allow new user registration when API times out
        return False
    except requests.exceptions.RequestException as e:
        print(f"Request error checking registration status: {str(e)}")
        # Return False to allow new user registration when API fails
        return False
    except Exception as e:
        print(f"Unexpected error checking registration status: {str(e)}")
        # Return False to allow new user registration when unexpected errors occur
        return False

def validate_pin(msisdn, pin, session):
    """Validates user PIN via Laravel API and stores bearer token in session."""
    if session is None:
        print(f"Error: validate_pin called with None session. Stack trace: {''.join(traceback.format_stack())}")
        return False
    
    print(f"Calling validate_pin with msisdn={msisdn}, pin={pin}, session={session}")
    pin_validation_url = "https://chiweto.ch/insurance/api/UssdAuthentication"
    try:
        payload = {'msisdn': msisdn, 'pin': pin}
        print(f"Sending PIN validation request: URL={pin_validation_url}, Payload={payload}")
        response = requests.post(pin_validation_url, json=payload, timeout=10, verify=False)
        response.raise_for_status()
        print(f"Raw response: Status={response.status_code}, Content={response.text[:200]}")
        
        try:
            result = response.json()
            print(f"PIN validation response: {result}")
        except json.decoder.JSONDecodeError as e:
            print(f"JSONDecodeError: Failed to parse response - {str(e)}, Content={response.text[:200]}")
            return False
        print(result)
        if result.get('success', False):
            return True
        return False

    except requests.exceptions.ConnectionError as e:
        print(f"Connection error validating PIN: {str(e)}")
        return False
    except requests.exceptions.Timeout as e:
        print(f"Timeout error validating PIN: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Request error validating PIN: {str(e)}")
        return False
    except Exception as e:
        print(f"Unexpected error validating PIN: {str(e)}")
        return False

def ussd_handler(request):
    """Handles USSD requests."""
    try:
        print(f"USSD handler called with request.session: {request.session}")
        msisdn = request.POST.get('msisdn')
        pin = request.POST.get('pin')
        session = request.session
        print(f"Extracted msisdn={msisdn}, pin={pin}")
        if not msisdn or not pin:
            print("Missing MSISDN or PIN")
            return generate_response_xml("Missing MSISDN or PIN.", 2)
        
        success = validate_pin(msisdn, pin, session)
        print(f"validate_pin returned: {success}")
        if success:
            response_text = handle_registered_user_menu(session)
            return generate_response_xml(response_text, 2)
        else:
            return generate_response_xml("Failed to validate PIN. Please try again.", 2)
    
    except Exception as e:
        print(f"USSD Handler Error: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)
    
def handle_registered_user_menu(session):
    """Handles menu for registered users."""
    session['current_step'] = 'registered_menu'
    session.modified = True
    return generate_response_xml(
        "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Bwelerani Koyambilira",
        2
    )

def handle_policy_status_menu(session, user_input, msisdn):
    """Handles the policy status submenu without token-based authentication, displaying insurance_type-livestock_type."""
    session['current_step'] = 'policy_status_menu'
    session.modified = True

    # Log session state and MSISDN
    session_msisdn = session.get('msisdn', 'None')
    print(f"Session state: keys={list(session.keys())}, session_msisdn={session_msisdn}, arg_msisdn={msisdn}")

    # Validate MSISDN
    if not msisdn or not isinstance(msisdn, str) or not msisdn.strip():
        print(f"Invalid or missing MSISDN: {msisdn}")
        return generate_response_xml(
            "Nambala ya foni yalephera. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )

    # Normalize MSISDN (remove '+' to match Postman format)
    msisdn = msisdn.lstrip('+').strip()
    print(f"Original MSISDN: {msisdn}, Normalized MSISDN: {msisdn}")

    # Validate MSISDN format
    if not msisdn.isdigit() or len(msisdn) < 10:
        print(f"Invalid MSISDN format: {msisdn}")
        return generate_response_xml(
            "Nambala ya foni yolakwika. Lowetsani nambala yoyenera.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )

    # Compare with expected MSISDN
    expected_msisdn = '265888920995'
    if msisdn != expected_msisdn:
        print(f"MSISDN mismatch: expected={expected_msisdn}, got={msisdn}")

    # Define status endpoints and labels
    status_endpoints = {
        '1': f'https://chiweto.ch/insurance/api/proposals/all?username={msisdn}&status=0',
        '2': f'https://chiweto.ch/insurance/api/proposals/all?username={msisdn}&status=1',
        '3': f'https://chiweto.ch/insurance/api/proposals/all?username={msisdn}&status=2',
        '4': f'https://chiweto.ch/insurance/api/proposals/all?username={msisdn}&status=3'
    }

    status_labels = {
        '0': 'Operekedwa',
        '1': 'Ololedwa',
        '2': 'Okanidwa',
        '3': 'Olipilidwa'
    }

    if user_input == '0':  # Go back to registered user menu
        print("Navigating back to registered user menu")
        return handle_registered_user_menu(session)
    elif user_input == '00':  # Exit
        print("Exiting USSD session")
        session['current_step'] = 'end'
        session.modified = True
        return generate_response_xml("Zikomo pogwiritsa ntchito Chiweto. Tsalani bwino.", 3)
    elif user_input in status_endpoints:
        try:
            endpoint = status_endpoints[user_input]
            print(f"Sending GET request to: {endpoint}")
            headers = {
                'Accept': 'application/json'
            }
            print(f"Request headers: {headers}")
            response = requests.get(endpoint, headers=headers, timeout=15, verify=True)
            response.raise_for_status()
            print(f"Response status: {response.status_code}, Headers: {response.headers}, Body: {response.text}")

            # Parse JSON response
            try:
                result = response.json()
            except ValueError as e:
                print(f"Invalid JSON response: {str(e)}, Raw response: {response.text}")
                return generate_response_xml(
                    "Zolakwika pa data ya seva. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
                    2
                )

            print(f"Parsed response: {result}")

            # Handle API response
            message = result.get('message', 'Palibe ma polise omwe apezeka.')
            if isinstance(result.get('data'), list) and result['data']:
                message = "Ma polise Anu:\n"
                for idx, policy in enumerate(result['data'], 1):
                    insurance_type = policy.get('insurance_type', 'Polise')
                    livestock_type = policy.get('livestock_type', 'Unknown')
                    policy_status = status_labels.get(str(policy.get('status')), 'Unknown')
                    message += f"{idx}. {insurance_type}-{livestock_type}\n"
            elif result.get('message'):
                message = result['message']
            message += "\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(message, 2)

        except HTTPError as e:
            status_code = e.response.status_code if hasattr(e, 'response') else 'Unknown'
            error_detail = e.response.text[:200] if hasattr(e, 'response') else str(e)
            error_headers = e.response.headers if hasattr(e, 'response') else {}
            print(f"HTTPError: status={status_code}, detail={error_detail}, headers={error_headers}")

            # Check for e150 service unavailable error
            if 'e150 service currently unavailable' in error_detail.lower():
                error_message = "Seva ya Chiweto sipezeka pakadali pano. Yesaninso pambuyo."
            elif status_code == 404:
                error_message = f"Nambala ya foni {msisdn} sinapezeke. Lembetsani akaunti kapena yesani nambala ina."
            elif status_code == 400:
                error_message = "Zolakwika pa nambala kapena mtundu wa polise."
            elif status_code == 503:
                error_message = "Seva ya Chiweto sipezeka pakadali pano. Yesaninso pambuyo."
            elif status_code == 500:
                error_message = "Vuto pa seva. Yesaninso pambuyo."
            else:
                error_message = f"Zolakwika (Code {status_code}). Yesaninso."

            # Try to extract API error message
            try:
                error_json = e.response.json()
                api_message = error_json.get('message', '')
                if api_message:
                    error_message = f"{api_message[:50]}. Yesaninso."
            except (ValueError, AttributeError):
                pass

            message = f"{error_message}\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(message, 2)
        except Timeout:
            print("Timeout fetching policy status")
            message = f"Seva yadutsa nthawi yake. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(message, 2)
        except SSLError:
            print("SSL error fetching policy status")
            message = f"Vuto pa chitsimikizo cha seva. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(message, 2)
        except RequestException as e:
            print(f"RequestException: {str(e)}")
            message = f"Palibe kulumikizana ndi seva: {str(e)[:50]}. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(message, 2)
    else:
        print(f"Invalid input: {user_input}")
        return generate_response_xml(
            "Chisankho chosayenera. Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
            2
        )

def generate_response_xml(message, response_type, **kwargs):
    """Generates XML response for USSD."""
    try:
        xml_response = ET.Element('ussd')
        ET.SubElement(xml_response, 'type').text = str(response_type)
        ET.SubElement(xml_response, 'msg').text = message
        for key, value in kwargs.items():
            ET.SubElement(xml_response, key).text = str(value)
        xml_str = ET.tostring(xml_response, encoding='utf-8').decode('utf-8')
        print(f"Generated XML response: {xml_str}")
        return HttpResponse(xml_str, content_type='text/xml', status=200)
    except Exception as e:
        print(f"Error generating XML: {str(e)}")
        return HttpResponse(
            "<ussd><type>2</type><msg>Service currently unavailable. Please try again later.</msg></ussd>",
            content_type='text/xml',
            status=200
        )

def handle_back_step(current_step, session):
    """Handles back navigation for all USSD steps."""
    try:
        if current_step == 1:
            return generate_response_xml("Tsekulani akaunti ndi dzina lanu lapachitupa (ID):\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        elif current_step == 2:
            return generate_response_xml("Ndinu mamuna kapena mkazi:\n1. Mamuna\n2. Mkazi\n0. Bwelerani\n00. Bwelerani Koyambilira", 2)
        elif current_step == 3:
            regions = session.get('regions', [])
            response_message = "Sankhani Chigawo Chomwe mumakhala:\n"
            for idx, region in enumerate(regions, 1):
                response_message += f"{idx}. {region}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        elif current_step == 4:
            return fetch_districts_and_respond(session.get('selected_region', ''), session)
        elif current_step == 5:
            return fetch_epas_and_respond(session.get('selected_district', ''), session)
        elif current_step == 'registered_menu':
            return generate_response_xml(
                "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Bwelerani Koyambilira",
                2
            )
        elif current_step == 'policy_status_menu':
            return generate_response_xml(
                "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif current_step == 'view_approved_policies':
            return fetch_approved_policies(session.get('msisdn', ''), session)
        elif current_step == 'view_policy_details':
            # Go back to approved policies list
            return fetch_approved_policies(session.get('msisdn', ''), session)
        elif current_step == 'select_payment_method':
            return fetch_payment_options(session)
        elif current_step == 'confirm_policy_payment':
            policy = session.get('selected_policy', {})
            payment_method = session.get('selected_payment_method', '')
            return generate_response_xml(
                f"Malipiro a {policy.get('insurance_type')}\nNjira: {payment_method}\nMtengo: MK{policy.get('value', 0)}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif current_step == 'process_policy_payment':
            # Go back to confirm payment
            policy = session.get('selected_policy', {})
            payment_method = session.get('selected_payment_method', '')
            return generate_response_xml(
                f"Malipiro a {policy.get('insurance_type')}\nNjira: {payment_method}\nMtengo: MK{policy.get('value', 0)}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif current_step == 'view_paid_policies':
            return fetch_paid_policies(session.get('msisdn', ''), session)
        elif current_step == 'buy_insurance':
            return fetch_livestocks_and_respond(session)
        elif current_step == 'buy_insurance_select':
            return fetch_insurance_types_and_respond(session)
        elif current_step == 'buy_select_payment_method':
            return fetch_payment_options(session)
        elif current_step == 'buy_confirm_payment':
            insurance_desc = session.get('selected_insurance', '')
            livestock_desc = session.get('selected_livestock', '')
            payment_method = session.get('selected_payment_method', '')
            price = session.get('insurance_price', 1000)
            return generate_response_xml(
                f"Malipiro a {insurance_desc} pa {livestock_desc}\nNjira: {payment_method}\nMtengo: MK{price}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        elif current_step == 'buy_process_payment':
            insurance_desc = session.get('selected_insurance', '')
            livestock_desc = session.get('selected_livestock', '')
            payment_method = session.get('selected_payment_method', '')
            price = session.get('insurance_price', 1000)
            return generate_response_xml(
                f"Malipiro a {insurance_desc} pa {livestock_desc}\nNjira: {payment_method}\nMtengo: MK{price}\n\n1. Eya\n2. Ayi\n0. Bwelerani\n00. Bwelerani Koyambilira",
                2
            )
        # Default: go to home
        return handle_restart()
    except Exception as e:
        print(f"Back Step Error: {str(e)}")
        return generate_response_xml("Zolakwika kubwerera. Yesaninso.", 2)

def handle_restart():
    """Handles restart command."""
    return generate_response_xml("Mubwerera kuyambiriro. Tsekulani akaunti ndi dzina lanu lapachitupa (ID):", 1)

def fetch_regions_and_respond(session):
    """Fetches regions from API and generates response."""
    laravel_url = "https://chiweto.ch/insurance/api/regions"
    try:
        api_response = requests.get(laravel_url, timeout=10, verify=False)
        if api_response.status_code == 200:
            regions = api_response.json()
            if not regions:
                return generate_response_xml("Palibe zigawo zomwe zapezeka. Yesaninso.", 2)
            session['regions'] = regions
            session.modified = True
            response_message = "Sankhani Chigawo Chomwe mumakhala:\n"
            for idx, region in enumerate(regions, 1):
                response_message += f"{idx}. {region}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        print(f"Error fetching regions: status={api_response.status_code}, response={api_response.text}")
        return generate_response_xml("Zolakwika pakutenga zigawo. Yesaninso.", 2)
    except RequestException as e:
        print(f"Error fetching regions: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def fetch_districts_and_respond(region, session):
    """Fetches districts for a region from API."""
    if not region or not isinstance(region, str):
        print("Invalid region provided")
        return generate_response_xml("Chigawo sichinatsimikizike. Yesaninso.", 2)

    try:
        # Check for cached districts
        if (session.get('region') == region and 
            isinstance(session.get('districts'), list) and 
            len(session.get('districts', [])) > 0):
            print(f"Using cached districts for region: {region}")
            return generate_district_list_response(session)

        # API Configuration
        laravel_url = f"https://chiweto.ch/insurance/api/districts?region={region}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        try:
            api_response = requests.get(laravel_url, headers=headers, timeout=10)
            if api_response.status_code == 200:
                districts = api_response.json()
                print(f"Raw district response: {districts}")
                if not isinstance(districts, list):
                    print(f"Invalid district data format: {type(districts)}")
                    return generate_response_xml("Zolakwika pa data ya maboma.", 2)
                if not districts:
                    return generate_response_xml(f"Palibe maboma omwe apezeka ku {region}. Yesaninso.", 2)
                
                # Normalize district data to ensure id and name
                nmdistricts = []
                for idx, district in enumerate(districts):
                    if isinstance(district, dict):
                        district_id = district.get('id', str(idx + 1))
                        district_name = district.get('name', f"District {idx + 1}")
                    else:
                        district_id = str(idx + 1)
                        district_name = str(district)
                    nmdistricts.append({'id': district_id, 'name': district_name})
                
                session.update({
                    'districts': nmdistricts,
                    'region': region,
                    'current_district_page': 1,
                    'total_district_pages': (len(nmdistricts) + 9) // 10,
                    'selected_district': None
                })
                session.modified = True
                print(f"Normalized {len(nmdistricts)} districts for region: {region}")
                return generate_district_list_response(session)

            elif 500 <= api_response.status_code < 600:
                print(f"Server error fetching districts: status={api_response.status_code}, response={api_response.text}")
                return generate_response_xml("Seva yavuta. Yesaninso patapita kanthawi.", 2)
            else:
                print(f"Error fetching districts: status={api_response.status_code}, response={api_response.text}")
                return generate_response_xml(f"Zolakwika: {api_response.status_code}", 2)

        except Timeout:
            print("Timeout fetching districts")
            return generate_response_xml("Seva yadutsa nthawi yake. Yesaninso.", 2)
        except SSLError:
            print("SSL error fetching districts")
            return generate_response_xml("Vuto pa chitsimikizo cha seva.", 2)
        except RequestException as e:
            print(f"Network error fetching districts: {str(e)}")
            return generate_response_xml("Palibe kulumikizana ndi seva.", 2)

    except Exception as e:
        print(f"Unexpected error in fetch_districts_and_respond: {str(e)} - {type(e)}")
        return generate_response_xml("Zolakwika zosazindikira. Yesaninso.", 2)

def generate_district_list_response(session):
    """Generates district list display with pagination."""
    try:
        page_size = 10
        current_page = session.get('current_district_page', 1)
        districts = session.get('districts', [])
        total_pages = session.get('total_district_pages', 1)

        if not isinstance(districts, list) or not districts:
            print("Invalid or empty districts in session")
            return generate_response_xml("Palibe maboma omwe apezeka. Yesaninso.", 2)

        current_page = max(1, min(current_page, total_pages))
        start_idx = (current_page - 1) * page_size
        current_districts = districts[start_idx:start_idx + page_size]

        response_msg = "Sankhani Boma lomwe mukukhala:\n"
        for i, district in enumerate(current_districts, 1):
            district_name = district.get('name', 'District') if isinstance(district, dict) else str(district)
            response_msg += f"{i}. {district_name}\n"

        response_msg += f"\nTsamba {current_page}/{total_pages}\n"
        if current_page > 1:
            response_msg += "99. Kumbuyo\n"
        if current_page < total_pages:
            response_msg += "98. Kutsogolo\n"
        response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"

        return generate_response_xml(response_msg, 2)

    except Exception as e:
        print(f"Display Error in generate_district_list_response: {str(e)}")
        return generate_response_xml("Zolakwika pakusonkhanitsa maboma.", 2)

def handle_district_navigation(user_input, session, region, msisdn):
    """Handles district navigation and selection."""
    try:
        print(f"Handling district navigation: user_input={user_input}, region={region}, msisdn={msisdn}, session_keys={list(session.keys())}")

        if not region or not isinstance(region, str):
            print("Invalid region provided")
            return generate_response_xml("Chigawo sichinatsimikizike.", 2)

        if 'districts' not in session or session.get('region') != region:
            print(f"Reinitializing session for region: {region}")
            response = fetch_districts_and_respond(region, session)
            if "Zolakwika" in response.text or "Palibe maboma" in response.text:
                print(f"Fetch districts failed: {response.text}")
                return response

        districts = session.get('districts', [])
        if not districts or not isinstance(districts, list):
            print(f"Invalid or empty districts: {districts}")
            return generate_response_xml(f"Palibe maboma omwe apezeka ku {region}.", 2)

        current_page = session.get('current_district_page', 1)
        total_pages = session.get('total_district_pages', 1)
        if not isinstance(current_page, int) or not isinstance(total_pages, int):
            print(f"Invalid pagination state: current_page={current_page}, total_pages={total_pages}")
            return generate_response_xml("Zolakwika pa tsamba. Yesaninso.", 2)

        user_input = user_input.strip().upper()
        if user_input in ['98', 'N']:  # Next page
            if current_page >= total_pages:
                print(f"Attempted navigation beyond last page: current_page={current_page}, total_pages={total_pages}")
                return generate_response_xml("Muli patsamba lomaliza.", 2)
            session['current_district_page'] = current_page + 1
            session.modified = True
            print(f"Navigating to next district page: new_page={session['current_district_page']}")
            return generate_district_list_response(session)

        elif user_input in ['99', 'P']:  # Previous page
            if current_page <= 1:
                print(f"Attempted navigation before first page: current_page={current_page}")
                return generate_response_xml("Muli patsamba loyamba.", 2)
            session['current_district_page'] = current_page - 1
            session.modified = True
            print(f"Navigating to previous district page: new_page={session['current_district_page']}")
            return generate_district_list_response(session)

        elif user_input.isdigit():  # District selection
            selected_num = int(user_input)
            page_size = 10
            start_idx = (current_page - 1) * page_size
            district_index = start_idx + selected_num - 1

            if 1 <= selected_num <= min(page_size, len(districts) - start_idx) and district_index < len(districts):
                selected_district = districts[district_index]
                if not isinstance(selected_district, dict) or 'name' not in selected_district:
                    print(f"Invalid district data at index {district_index}: {selected_district}")
                    return generate_response_xml("District data yolakwika.", 2)
                session['selected_district'] = selected_district['name']
                session['current_step'] = 5
                session.modified = True
                print(f"Selected district: {session['selected_district']}")
                return fetch_epas_and_respond(session['selected_district'], session)
            print(f"Invalid district selection: selected_num={selected_num}, district_index={district_index}, districts_length={len(districts)}")
            return generate_response_xml("Nambala yolakwika patsambalo.", 2)

        elif user_input == '0':  # Back to region selection
            print("Handling back step")
            session['current_step'] = 3
            session.modified = True
            return handle_back_step(session['current_step'], session)
        elif user_input == '00':  # Restart
            print("Handling restart")
            session['current_step'] = 1
            session.modified = True
            return handle_restart()

        print(f"Invalid input received: {user_input}")
        return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)

    except Exception as e:
        print(f"Navigation Error in handle_district_navigation: {str(e)} - {type(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

def fetch_epas_and_respond(district, session):
    """Fetches EPAs with error handling and session management."""
    if not district or not isinstance(district, str):
        print("Invalid district provided")
        return generate_response_xml("Boma silinatsimikizike. Yesaninso.", 2)

    try:
        # Check for cached EPAs
        if (session.get('district') == district and 
            isinstance(session.get('epas'), list) and 
            len(session.get('epas', [])) > 0):
            print(f"Using cached EPAs for district: {district}")
            return generate_epa_list_response(session)

        # API Configuration
        api_url = f"https://chiweto.ch/insurance/api/epas?district={district}"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        try:
            response = requests.get(api_url, headers=headers, timeout=20)
            if response.status_code == 200:
                epas = response.json()
                print(f"Raw EPA response: {epas}")
                # Validate EPA format
                if not isinstance(epas, list):
                    print(f"Invalid EPA data format: {type(epas)}")
                    return generate_response_xml("Zolakwika pa data ya EPA.", 2)
                if not epas:
                    return generate_response_xml(f"Palibe ma EPA omwe apezeka ku {district}.", 2)
                
                # Normalize EPA data to ensure id and name
                nmepas = []
                for idx, epa in enumerate(epas):
                    if isinstance(epa, dict):
                        epa_id = epa.get('id', str(idx + 1))
                        epa_name = epa.get('name', f"EPA {idx + 1}")
                    else:
                        epa_id = str(idx + 1)
                        epa_name = str(epa)
                    nmepas.append({'id': epa_id, 'name': epa_name})
                
                session.update({
                    'epas': nmepas,
                    'district': district,
                    'current_page': 1,
                    'total_pages': (len(nmepas) + 9) // 10,
                    'selected_epa': None
                })
                session.modified = True
                print(f"Normalized {len(nmepas)} EPAs for district: {district}")
                return generate_epa_list_response(session)

            elif 500 <= response.status_code < 600:
                print(f"Server error fetching EPAs: status={response.status_code}, response={response.text}")
                return generate_response_xml("Seva yavuta. Yesaninso patapita kanthawi.", 2)
            else:
                print(f"Error fetching EPAs: status={response.status_code}, response={response.text}")
                return generate_response_xml(f"Zolakwika: {response.status_code}", 2)

        except Timeout:
            print("Timeout fetching EPAs")
            return generate_response_xml("Seva yadutsa nthawi yake. Yesaninso.", 2)
        except SSLError:
            print("SSL error fetching EPAs")
            return generate_response_xml("Vuto pa chitsimikizo cha seva.", 2)
        except RequestException as e:
            print(f"Network error fetching EPAs: {str(e)}")
            return generate_response_xml("Palibe kulumikizana ndi seva.", 2)

    except Exception as e:
        print(f"Unexpected error in fetch_epas_and_respond: {str(e)} - {type(e)}")
        return generate_response_xml("Zolakwika zosazindikira. Yesaninso.", 2)

def generate_epa_list_response(session):
    """Generates EPA list display with pagination."""
    try:
        page_size = 10
        current_page = session.get('current_page', 1)
        epas = session.get('epas', [])
        total_pages = session.get('total_pages', 1)

        if not isinstance(epas, list) or not epas:
            print("Invalid or empty EPAs in session")
            return generate_response_xml("Palibe ma EPA omwe apezeka. Yesaninso.", 2)

        current_page = max(1, min(current_page, total_pages))
        start_idx = (current_page - 1) * page_size
        current_epas = epas[start_idx:start_idx + page_size]

        response_msg = "Sankhani EPA:\n"
        for i, epa in enumerate(current_epas, 1):
            epa_name = epa.get('name', 'EPA') if isinstance(epa, dict) else str(epa)
            response_msg += f"{i}. {epa_name}\n"

        response_msg += f"\nTsamba {current_page}/{total_pages}\n"
        if current_page > 1:
            response_msg += "99. Kumbuyo\n"
        if current_page < total_pages:
            response_msg += "98. Kutsogolo\n"
        response_msg += "0. Bwelerani\n00. Bwelerani Koyambilira"

        return generate_response_xml(response_msg, 2)

    except Exception as e:
        print(f"Display Error in generate_epa_list_response: {str(e)}")
        return generate_response_xml("Zolakwika pakusonkhanitsa ma EPA.", 2)

def handle_epa_navigation(user_input, session, district, msisdn):
    """Handles EPA navigation and selection, directly submits registration on selection."""
    try:
        print(f"Handling EPA navigation: user_input={user_input}, district={district}, msisdn={msisdn}, session_keys={list(session.keys())}")

        if not district or not isinstance(district, str):
            print("Invalid district provided")
            return generate_response_xml("Boma silinatsimikizike.", 2)

        if 'epas' not in session or session.get('district') != district:
            print(f"Reinitializing session for district: {district}")
            response = fetch_epas_and_respond(district, session)
            if "Zolakwika" in response.text or "Palibe ma EPA" in response.text:
                print(f"Fetch EPAs failed: {response.text}")
                return response

        epas = session.get('epas', [])
        if not epas or not isinstance(epas, list):
            print(f"Invalid or empty EPAs: {epas}")
            return generate_response_xml(f"Palibe ma EPA omwe apezeka ku {district}.", 2)

        current_page = session.get('current_page', 1)
        total_pages = session.get('total_pages', 1)
        if not isinstance(current_page, int) or not isinstance(total_pages, int):
            print(f"Invalid pagination state: current_page={current_page}, total_pages={total_pages}")
            return generate_response_xml("Zolakwika pa tsamba. Yesaninso.", 2)

        user_input = user_input.strip().upper()
        if user_input in ['98', 'N']:  # Next page
            if current_page >= total_pages:
                print(f"Attempted navigation beyond last page: current_page={current_page}, total_pages={total_pages}")
                return generate_response_xml("Muli patsamba lomaliza.", 2)
            session['current_page'] = current_page + 1
            session.modified = True
            print(f"Navigating to next page: new_page={session['current_page']}")
            return generate_epa_list_response(session)

        elif user_input in ['99', 'P']:  # Previous page
            if current_page <= 1:
                print(f"Attempted navigation before first page: current_page={current_page}")
                return generate_response_xml("Muli patsamba loyamba.", 2)
            session['current_page'] = current_page - 1
            session.modified = True
            print(f"Navigating to previous page: new_page={session['current_page']}")
            return generate_epa_list_response(session)

        elif user_input.isdigit():  # EPA selection
            selected_num = int(user_input)
            page_size = 10
            start_idx = (current_page - 1) * page_size
            epa_index = start_idx + selected_num - 1

            if 1 <= selected_num <= min(page_size, len(epas) - start_idx) and epa_index < len(epas):
                selected_epa = epas[epa_index]
                if not isinstance(selected_epa, dict) or 'id' not in selected_epa or 'name' not in selected_epa:
                    print(f"Invalid EPA data at index {epa_index}: {selected_epa}")
                    return generate_response_xml("Ma EPA yolakwika.", 2)
                session['selected_epa'] = {
                    'id': str(selected_epa.get('id')),
                    'name': selected_epa.get('name', 'EPA')
                }
                session.modified = True
                print(f"Selected EPA: {session['selected_epa']}")
                return submit_farmer_registration(session, msisdn)
            print(f"Invalid EPA selection: selected_num={selected_num}, epa_index={epa_index}, epas_length={len(epas)}")
            return generate_response_xml("Nambala yolakwika patsambalo.", 2)

        elif user_input == '0':  # Back to district selection
            print("Handling back step")
            session['current_step'] = 4
            session.modified = True
            return handle_back_step(session['current_step'], session)
        elif user_input == '00':  # Restart
            print("Handling restart")
            session['current_step'] = 1
            session.modified = True
            return handle_restart()

        print(f"Invalid input received: {user_input}")
        return generate_response_xml("Chisankho chosayenera. Yesaninso.", 2)

    except Exception as e:
        print(f"Navigation Error: {str(e)} - {type(e)}")
        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

def fetch_livestocks_and_respond(session):
    """Fetches livestock from API."""
    laravel_url = "https://chiweto.ch/insurance/api/livestock/get_all"
    try:
        api_response = requests.get(laravel_url, timeout=10)
        if api_response.status_code == 200:
            livestock_data = api_response.json()
            if not livestock_data:
                return generate_response_xml("Palibe ziweto zomwe zapezeka.", 2)
            session['livestock_data'] = livestock_data
            session['livestock'] = [livestock['description'] for livestock in livestock_data]
            session.modified = True
            response_message = "Sankhani Chiweto:\n"
            for idx, livestock in enumerate(session['livestock'], 1):
                response_message += f"{idx}. {livestock}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        print(f"Error fetching livestock: status={api_response.status_code}, response={api_response.text}")
        return generate_response_xml("Zolakwika pakutenga ziweto. Yesaninso.", 2)
    except RequestException as e:
        print(f"Error fetching livestock: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def fetch_insurance_types_and_respond(session):
    """Fetches insurance types from API."""
    laravel_url = "https://chiweto.ch/insurance/api/livestock/get_insurance_type"
    try:
        api_response = requests.get(laravel_url, timeout=10)
        if api_response.status_code == 200:
            insurance_data = api_response.json()
            if not insurance_data:
                return generate_response_xml("Palibe mitundu ya inshulansi yomwe yapezeka.", 2)
            session['insurance_data'] = insurance_data
            session['insurance'] = [insurance['description'] for insurance in insurance_data]
            session.modified = True
            response_message = "Sankhani mtundu wa inshulansi:\n"
            for idx, insurance in enumerate(session['insurance'], 1):
                response_message += f"{idx}. {insurance}\n"
            response_message += "0. Bwelerani\n00. Bwelerani Koyambilira"
            return generate_response_xml(response_message, 2)
        print(f"Error fetching insurance types: {api_response.status_code}, response={api_response.text}")
        return generate_response_xml("Zolakwika pakutenga mitundu ya inshowa. Yesaninso.", 2)
    except RequestException as e:
        print(f"Error fetching insurance types: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

def submit_insurance_data(session, msisdn):
    """Submits insurance selection to API."""
    livestock_type = session.get('selected_livestock_id', '')
    insurance_type = session.get('selected_insurance_id', '')
    if not all([livestock_type, insurance_type, msisdn]):
        print(f"Missing insurance data: livestock_type={livestock_type}, insurance_type={insurance_type}, msisdn={msisdn}")
        return generate_response_xml("Zolakwika pa data ya inshulansi.", 2)

    laravel_url = "https://chiweto.ch/insurance/api/proposal/add_ussd"
    data = {
        'phone_number': msisdn,
        'phone': msisdn,
        'insurance_type': insurance_type,
        'livestock_type': livestock_type,
    }
    headers = {'Content-Type': 'application/json'}
    try:
        api_response = requests.post(laravel_url, json=data, headers=headers, timeout=10, verify=False)
        if api_response.status_code == 200:
            return generate_response_xml("Kulembetsa inshulansi kwatheka.\n0. Bwelerani\n00.Bwelerani Koyambilira", 2)
        print(f"Error submitting insurance: status={api_response.status_code}, response={api_response.text}")
        return generate_response_xml("Zolakwika pakulembetsa inshulansi. Yesaninso.\n0. Bwelerani\n00.Bwelerani Koyambilira", 2)
    except RequestException as e:
        print(f"Error submitting insurance: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.\n0. Bwelerani\n00.Bwelerani Koyambilira", 2)

def submit_farmer_registration(session, msisdn):
    """Submits farmer registration to API with retry logic."""
    data = {
        'phone': msisdn,
        'name': session.get('farmer_name', '').strip(),
        'gender': session.get('farmer_gender', ''),
        'region': session.get('selected_region', ''),
        'district': session.get('selected_district', ''),
        'epa': session.get('selected_epa', {}).get('name', '')
    }
    if not all([data[key] for key in data]):
        print(f"Missing registration data: {data}")
        return generate_response_xml("Zolakwika pa data ya kulembetsa.", 2)

    laravel_url = "https://chiweto.ch/insurance/api/register_client_ussd"
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            print(f"Submitting registration attempt {attempt + 1}: url={laravel_url}, data={data}, headers={headers}")
            response = requests.post(laravel_url, json=data, headers=headers, timeout=15, verify=False)
            print(f"Registration response: status={response.status_code}, body={response.text}")
            
            if response.status_code == 200:
                return generate_response_xml("Kutsekula akaunti kwatheka. Zikomo!", 3)
            
            error_msg = response.json().get('message', response.text) if response.text else 'No response body'
            print(f"Error registering farmer: status={response.status_code}, response={error_msg}")
            if response.status_code >= 500 and attempt < max_retries - 1:
                print(f"Server error, retrying... (attempt {attempt + 1})")
                continue
            return generate_response_xml(
                f"Kulembetsa akaunti kwakanika: {error_msg[:50]}. Yesaninso.", 2
            )
            
        except Timeout:
            print(f"Timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                continue
            return generate_response_xml("Seva yadutsa nthawi yake. Yesaninso.", 2)
        except SSLError:
            print(f"SSL error on attempt {attempt + 1}")
            return generate_response_xml("Vuto pa chitsimikizo cha seva.", 2)
        except RequestException as e:
            print(f"Network error on attempt {attempt + 1}: {str(e)}")
            return generate_response_xml("Palibe kulumikizana ndi seva.", 2)
        except ValueError:
            print(f"Invalid JSON response on attempt {attempt + 1}: {response.text}")
            return generate_response_xml("Zolakwika pa seva ya data. Yesaninso.", 2)

    return generate_response_xml("Kulembetsa akaunti kwakanika. Yesaninso.", 2)