import xml.etree.ElementTree as ET
import requests
import json
import traceback
from requests.exceptions import RequestException, Timeout, SSLError, HTTPError
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

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
            current_step = max(1, current_step - 1) if isinstance(current_step, int) else 1
            session['current_step'] = current_step
            session.modified = True
            return handle_back_step(current_step, session)
        if msg == '00':  # Return to home
            session['current_step'] = 1
            session.modified = True
            return handle_restart()

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
                if validate_pin(msisdn, pin,session):
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
                        "Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani",
                        2
                    )
                elif msg == '4':
                    session['current_step'] = 'end'
                    session.modified = True
                    return generate_response_xml("Zikomo pogwiritsa ntchito Chiweto. Tsalani bwino.", 3)
                return generate_response_xml(
                    "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Tulukani",
                    2
                )

            elif current_step == 'policy_status_menu':
                return handle_policy_status_menu(session, msg, msisdn)

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
                response_message += "0. Bwelerani\n00. Koyambilira"
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
                response_message += "0. Bwelerani\n00. Koyambilira"
                return generate_response_xml(response_message, 2)

        return generate_response_xml("Zolakwika zidachitika. Yesaninso.", 2)

    except Exception as e:
        print(f"USSD Handler Error: {str(e)} - {type(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

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
    except RequestException as e:
        print(f"Error checking registration status: {str(e)}")
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

    except (RequestException, HTTPError) as e:
        print(f"Error validating PIN: {str(e)}")
        return False

# def validate_pin(msisdn, pin, session):
#     """Validates user PIN via Laravel API and stores bearer token in session."""
#     if session is None:
#         print(f"Error: validate_pin called with None session. Stack trace: {''.join(traceback.format_stack())}")
#         return False

#     print(f"Calling validate_pin with msisdn={msisdn}, pin={pin}, session_keys={list(session.keys())}")
#     pin_validation_url = "https://chiweto.ch/insurance/api/UssdAuthentication"
#     try:
#         # Normalize inputs
#         msisdn = msisdn.strip().lstrip('+')
#         pin = pin.strip()
#         payload = {'msisdn': msisdn, 'pin': pin, 'status': 1}
#         headers = {
#             'Accept': 'application/json',
#             'Content-Type': 'application/json',
#             'User-Agent': 'USSD-Client/1.0'  # Match Postman’s User-Agent if needed
#         }
#         print(f"Sending PIN validation request: URL={pin_validation_url}, Payload={payload}, Headers={headers}")
#         # Try with verify=True, fallback to verify=False if SSL fails
#         try:
#             response = requests.post(pin_validation_url, json=payload, headers=headers, timeout=15, verify=True)
#         except SSLError as ssl_err:
#             print(f"SSL verification failed: {str(ssl_err)}. Retrying with verify=False")
#             response = requests.post(pin_validation_url, json=payload, headers=headers, timeout=15, verify=False)

#         print(f"Raw response: Status={response.status_code}, Content={response.text[:1000]}")
#         response.raise_for_status()

#         try:
#             result = response.json()
#             print(f"PIN validation response: {result}")
#         except json.decoder.JSONDecodeError as e:
#             print(f"JSONDecodeError: Failed to parse response - {str(e)}, Content={response.text[:1000]}")
#             return False

#         success = result.get('success', False) or result.get('status') == 'success'
#         token = result.get('token') or result.get('access_token') or result.get('data', {}).get('token')
#         message = result.get('message', 'No message provided')
#         print(f"Success: {success}, Token: {token[:10] if token else 'None'}, Message: {message}")

#         if success and token:
#             session['bearer_token'] = token
#             session['msisdn'] = msisdn
#             session.modified = True
#             print(f"Stored bearer token in session: {token[:10]}..., session_keys={list(session.keys())}")
#             return True
#         else:
#             print(f"Authentication failed: success={success}, token={token}, message={message}")
#             return False

#     except HTTPError as e:
#         status_code = e.response.status_code if hasattr(e, 'response') else 'Unknown'
#         error_detail = e.response.text[:1000] if hasattr(e, 'response') else str(e)
#         print(f"HTTPError: Status={status_code}, Detail={error_detail}")
#         return False
#     except RequestException as e:
#         print(f"RequestException: {str(e)}")
#         return False

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
        "Sankhani chomwe mukufuna:\n1. Gulani Inshulansi\n2. Itanani Mlangizi\n3. Ma Polise omwe muli nawo\n4. Tulukani",
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
            "Nambala ya foni yalephera. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani",
            2
        )

    # Normalize MSISDN (remove '+' to match Postman format)
    msisdn = msisdn.lstrip('+').strip()
    print(f"Original MSISDN: {msisdn}, Normalized MSISDN: {msisdn}")

    # Validate MSISDN format
    if not msisdn.isdigit() or len(msisdn) < 10:
        print(f"Invalid MSISDN format: {msisdn}")
        return generate_response_xml(
            "Nambala ya foni yolakwika. Lowetsani nambala yoyenera.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani",
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
                    "Zolakwika pa data ya seva. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani",
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
            message += "\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani"
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

            message = f"{error_message}\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani"
            return generate_response_xml(message, 2)
        except Timeout:
            print("Timeout fetching policy status")
            message = f"Seva yadutsa nthawi yake. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani"
            return generate_response_xml(message, 2)
        except SSLError:
            print("SSL error fetching policy status")
            message = f"Vuto pa chitsimikizo cha seva. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani"
            return generate_response_xml(message, 2)
        except RequestException as e:
            print(f"RequestException: {str(e)}")
            message = f"Palibe kulumikizana ndi seva: {str(e)[:50]}. Yesaninso.\n\nSankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani"
            return generate_response_xml(message, 2)
    else:
        print(f"Invalid input: {user_input}")
        return generate_response_xml(
            "Chisankho chosayenera. Sankhani mtundu wa polise:\n1. Operekedwa\n2. Ololedwa\n3. Okanidwa\n4. Olipilidwa\n0. Bwelerani\n00. Tulukani",
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
    """Handles back navigation."""
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
        return generate_response_xml("Zolakwika kubwerera. Yesaninso.", 2)
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
        response_msg += "0. Bwelerani\n00. Koyambirira"

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
        response_msg += "0. Bwelerani\n00. Koyambirira"

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
                    return generate_response_xml("EPA data yolakwika.", 2)
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
            response_message = "Sankhani ziweto:\n"
            for idx, livestock in enumerate(session['livestock'], 1):
                response_message += f"{idx}. {livestock}\n"
            response_message += "0. Bwelerani\n00. Koyambirira"
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
                return generate_response_xml("Palibe mitundu ya inshowa yomwe yapezeka.", 2)
            session['insurance_data'] = insurance_data
            session['insurance'] = [insurance['description'] for insurance in insurance_data]
            session.modified = True
            response_message = "Sankhani mtundu wa inshulansi:\n"
            for idx, insurance in enumerate(session['insurance'], 1):
                response_message += f"{idx}. {insurance}\n"
            response_message += "0. Bwelerani\n00. Koyambirira"
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
            return generate_response_xml("Kulembetsa inshulansi kwatheka.", 2)
        print(f"Error submitting insurance: status={api_response.status_code}, response={api_response.text}")
        return generate_response_xml("Zolakwika pakulembetsa inshulansi. Yesaninso.", 2)
    except RequestException as e:
        print(f"Error submitting insurance: {str(e)}")
        return generate_response_xml("Service currently unavailable. Please try again later.", 2)

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