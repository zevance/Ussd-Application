<?php

namespace App\Http\Controllers;

use App\Models\InsuranceProposal;
use App\Models\Premium;
use App\Models\ProposalDetail;
use App\Models\User;
use Dompdf\Dompdf;
use Dompdf\Options;
use Illuminate\Http\Request;
use DB;
use DateTime;
use App\Models\Payment;
use App\Models\InsuranceType;
use App\Models\LivestockType;
use App\Models\PaymentMethod;
use App\Models\EndPointResponse;
use App\Models\PawaPayment;
use Illuminate\Support\Facades\Http;
use App\Services\FCMService;
use Exception;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\Storage;
use Ramsey\Uuid\Uuid;
use Ramsey\Uuid\Rfc4122\FieldsInterface;
use Carbon\Carbon;
use Illuminate\Support\Str; 

class PaymentsController extends Controller
{
    public function DownloadProposalPDF($id)
    {
        // Fetch the proposal details
        $proposalDetail = \App\Models\ProposalDetail::find($id);
        if (!$proposalDetail) {
            return response()->json(['error' => 'Proposal not found.'], 404);
        }

        // Generate the PDF content
        $dompdf = new Dompdf();
        $options = new Options();
        $options->set('isHtml5ParserEnabled', true);
        $dompdf->setOptions($options);

        $html = view('pdf.proposal', compact('proposalDetail'))->render();
        $dompdf->loadHtml($html);
        $dompdf->setPaper('A4', 'portrait');
        $dompdf->render();

        // Ensure the directory exists
        $directory = 'proposals';
        if (!Storage::disk('public')->exists($directory)) {
            Storage::disk('public')->makeDirectory($directory);
            Log::info('Directory created: ' . $directory);
        } else {
            Log::info('Directory already exists: ' . $directory);
        }

        // Save the PDF to a temporary file
        $pdfContent = $dompdf->output();
        $filePath = $directory . '/proposal_' . $id . '.pdf';
        Storage::disk('public')->put($filePath, $pdfContent);
        Log::info('Attempted to save PDF file: ' . $filePath);

        // Verify the file was saved
        if (!Storage::disk('public')->exists($filePath)) {
            Log::error('Failed to save PDF file: ' . $filePath);
            return response()->json(['error' => 'Failed to save PDF file.'], 500);
        }

        Log::info('PDF file saved successfully: ' . $filePath);

        // Download the PDF file
        $response = Storage::disk('public')->download($filePath);

//        // Delete the file after download
//        Storage::disk('public')->delete($filePath);
//        Log::info('PDF file deleted after download: ' . $filePath);

        return $response;
    }

    public function DownloadProof($filename)
    {
        $filePath = 'proofs/' . $filename;

        if (!Storage::disk('public')->exists($filePath)) {
            return response()->json(['error' => 'File not found.'], 404);
        }

        return Storage::disk('public')->download($filePath);
    }
    public function UploadProof(Request $request)
    {
        $request->validate([
            'image' => 'required|image|mimes:jpeg,png,jpg,gif|max:2048',
        ]);

        $originalName = $request->file('image')->getClientOriginalName();
        $fileInfo = pathinfo($originalName);
        $proposal_id = $fileInfo['filename'];
        $path = $request->file('image')->storeAs('proofs', $originalName, 'public');

        do {
            $transaction_id = strtoupper($this->unique_code());
        } while (DB::table('payments')
            ->where('transaction_id', $transaction_id)->exists()
        );

        $proposal = InsuranceProposal::find($proposal_id);
        $proposal->payment_proof = $originalName;
        $proposal->transaction_id = $transaction_id;
        $proposal->status = 3;
        $proposal->save();

        $date_created       = Carbon::now('CAT');
        $expiry_date        = Carbon::now('CAT')->addYear(1);

        $client = InsuranceProposal::where('id', $proposal_id)->first();

        $payment = new Payment;
        $payment->client = $client['customer'];
        $payment->paid_by = $client['customer'];
        $payment->transaction_id = $transaction_id;
        $payment->insurance_type = 2;
        $payment->livestock_type = 1;
        $payment->livestock_quantity = 1;
        $payment->duration = 1;
        $payment->total_amount = $this->CalculateFees($client['value']);
        $payment->payment_method = 102;
        $payment->date_paid = $date_created;
        $payment->expiry_date = $expiry_date;
        $payment->proposal_id = $proposal_id;
        $payment->status = 1;
        $payment->save();

        return response()->json(['path' => $path], 201);
    }

    public function CalculateFees($value){
        $calc = Premium::where('insurance_type_local', 2)
            ->first();

        $minimum = $calc->minimum_limit;
        $basic_rate = $calc->basic_rate;
        $basic_limit = $calc->basic_limit;
        $gold_rate = $calc->gold_rate;
        $gold_limit = $calc->gold_limit;
        $stamp_duty = $calc->stamp_duty;
        $vat = $calc->vat;
        $admin_rate = $calc->admin_fee_rate;
        $discount = $calc->discount;

        if ($value >= $minimum) {
            if ($value > $basic_limit) {
                $rate = $gold_rate;
            } else {
                $rate = $basic_rate;
            }

            $cost_after_rate = $value * ($rate/100);
            $vat_cost =  $cost_after_rate * ($vat / 100);
            $sub_total = $cost_after_rate + $vat_cost + $stamp_duty;
            $admin_fee = ($admin_rate / 100) * $sub_total;
            $total = $sub_total + $admin_fee;
            $total_premium = number_format($total - (($discount / 100 ) * $total), 2, '.', '' );
        } else {
            $total_premium = "0.0";
        }

        return $total_premium;
    }

    public function ResubmitProposal(Request $request)
    {
        try {

            $proposal = InsuranceProposal::find($request->proposal_id);
            $proposal->customer = $request->customer;
            $proposal->created_by = $request->created_by;
            $proposal->insurance_type = $request->insurance_type;
            $proposal->livestock_type = LivestockType::where("description", $request->livestock_type)->first()->id;
            $proposal->value = $request->value;
            $proposal->animal_id = $request->animal_id;
            $proposal->gender = $request->gender;
            $proposal->age = $request->age;
            $proposal->farm_address = $request->farm_address;
            $proposal->stabled_at_night = $request->stabled_at_night;
            $proposal->enclosed_paddock = $request->enclosed_paddle;
            $proposal->open_range = $request->open_range;
            $proposal->constant_supervision = $request->constant_supervision;
            $proposal->purpose_of_animal = $request->purpose_of_animal;
            $proposal->sound_health = $request->sound_healthy;
            $proposal->defects_past_twelve = $request->defects_past_twelve;
            $proposal->contagious_diseases_in_vicinity = $request->contagious_diseases_in_vicinity;
            $proposal->details_of_disease_in_vicinity = $request->details_of_disease_in_vicinity;
            $proposal->disease_past_twelve = $request->disease_past_twelve;
            $proposal->disease_past_twelve_details = $request->disease_past_twelve_details;
            $proposal->duration_in_possession = $request->duration_in_possession;
            $proposal->animals_imported_into_district = $request->animals_imported_into_district;
            $proposal->animals_imported_from = $request->animals_imported_from;
            $proposal->pregnant = $request->pregnant;
            if ($request->pregnant == "Yes") {
                $proposal->date_due_to_calve = $request->date_due_to_calve;
                $proposal->year_of_last_calving = $request->year_of_last_calving;
                $proposal->pregnant_covering_fee = $request->pregnant_covering_fee;
            }
            $proposal->birth_lost = $request->birth_lost;
            $proposal->same_species = $request->same_species;
            $proposal->cover_losses_calving = $request->cover_losses_calving;
            $proposal->status = 0;
            $proposal->save();

            return response()->json(['success' => true, 'message' => 'Proposal submitted successfully']);
        } catch (Exception $e) {
            return response()->json(['success' => false, 'message' => $e->getMessage()], 500);
        }

    }
    public function ReviewProposal(Request $request) {
        $proposal = InsuranceProposal::where('id', $request->proposal_id)->first();
        $proposal->status = $request->status;
        $proposal->reviewer = $request->reviewer;
        $proposal->review_pregnant = $request->pregnant_review;
        $proposal->review_abortion = $request->abortion_history_review;
        $proposal->review_pulse = $request->pulse;
        $proposal->review_eyes_perfect = $request->eyes;
        $proposal->review_lameness = $request->lameness;
        $proposal->review_colic = $request->colic;
        $proposal->review_operation = $request->operation;
        $proposal->review_contagious = $request->contagious;
        $proposal->review_date_recovered_impact = $request->operation_details;
        $proposal->review_expextant_date_details = $request->expextant_date;
        $proposal->reviewed_on = Carbon::now()->toDateString();

        $proposal->save();
        return response()->json(['success' => true, 'message' => 'Proposal reviewed successfully']);
    }
    public function ProposalDetails(Request $request)
    {
        $details = ProposalDetail::where('id', $request->proposal_id)->first();
        return response()->json(['success' => true, 'data' => $details]);
    }
    public function GetProposals(Request $request)
    {
        $user = User::where('username', $request->username)->first();
        $usertype = $user['user_type'];
        $epa = $user['institution'];
        $status = $request->status;

        try {
            if ($usertype == 0) {
                $proposals = ProposalDetail::where('status', $status)->get();
            } else if ($usertype == 1) {
                $proposals = ProposalDetail::where('epa_id', $epa)
                                            ->where('status', $status)
                                            ->get();
            } else {
                $proposals = ProposalDetail::where('customer', $request->username)
                                            ->where('status', $status)
                                            ->get();
            }

            return response()->json(['success' => true, 'data' => $proposals]);
        } catch (Exception $e) {
            return response()->json(['success' => false, 'message' => $e->getMessage()], 500);
        }
    }
    public function AddProposal(Request $request) {
        try {

            $proposal = new InsuranceProposal;
            $proposal->customer = $request->customer;
            $proposal->created_by = $request->created_by;
            $proposal->insurance_type = $request->insurance_type;
            $proposal->livestock_type = LivestockType::where("description", $request->livestock_type)->first()->id;
            $proposal->value = $request->value;
            $proposal->animal_id = $request->animal_id;
            $proposal->gender = $request->gender;
            $proposal->age = $request->age;
            $proposal->farm_address = $request->farm_address;
            $proposal->stabled_at_night = $request->stabled_at_night;
            $proposal->enclosed_paddock = $request->enclosed_paddle;
            $proposal->open_range = $request->open_range;
            $proposal->constant_supervision = $request->constant_supervision;
            $proposal->purpose_of_animal = $request->purpose_of_animal;
            $proposal->sound_health = $request->sound_healthy;
            $proposal->defects_past_twelve = $request->defects_past_twelve;
            $proposal->contagious_diseases_in_vicinity = $request->contagious_diseases_in_vicinity;
            $proposal->details_of_disease_in_vicinity = $request->details_of_disease_in_vicinity;
            $proposal->disease_past_twelve = $request->disease_past_twelve;
            $proposal->disease_past_twelve_details = $request->disease_past_twelve_details;
            $proposal->duration_in_possession = $request->duration_in_possession;
            $proposal->animals_imported_into_district = $request->animals_imported_into_district;
            $proposal->animals_imported_from = $request->animals_imported_from;
            $proposal->pregnant = $request->pregnant;
            $proposal->date_due_to_calve = $request->date_due_to_calve;
            $proposal->year_of_last_calving = $request->year_of_last_calving;
            $proposal->birth_lost = $request->birth_lost;
            $proposal->same_species = $request->same_species;
            $proposal->cover_losses_calving = $request->cover_losses_calving;
            $proposal->pregnant_covering_fee = $request->pregnant_covering_fee;
            $proposal->save();

            return response()->json(['success' => true, 'message' => 'Proposal submitted successfully']);
        } catch (Exception $e) {
            return response()->json(['success' => false, 'message' => $e->getMessage()], 500);
        }



    }
    public function GetPaymentOptions(Request $request)
    {

        $result = DB::table('payment_methods')
            ->pluck('description');
        return $result;
    }

    public function PayFees(Request $request)
{
    // Log the incoming request for debugging
    \Log::info('PayFees request:', $request->all());

    // 1. Normalize and validate phone numbers
    $user = $this->normalizeMsisdn($request->user);
    $client = $this->normalizeMsisdn($request->phone_number);

    if (!$user || !$client) {
        return response()->json([
            'error_type' => "Validation",
            'error_status' => "INVALID_MSISDN",
            'error_message' => "The destination number you have entered is invalid. Type the number correctly."
        ], 400);
    }

    // 2. Generate a unique transaction ID
    do {
        $transaction_id = strtoupper($this->unique_code());
    } while (DB::table('payments')->where('transaction_id', $transaction_id)->exists());

    $insurance_type = $request->insurance_type;
    $livestock_quantity = $request->livestock_quantity;

    // 3. Get insurance and payment method IDs
    $insuranceTypeModel = InsuranceType::where('description', $request->insurance_type)->first();
    if ($insuranceTypeModel) {
        $insurance_type = $insuranceTypeModel->id;
    }
    $livestock_type = LivestockType::where('description', $request->livestock_type)->first()->id;
    $payment_method = PaymentMethod::where('description', $request->payment_method)->first()->code;

    $desc = "qty " . $livestock_quantity . " ls " . $livestock_type;
    $total_amount_fees  = str_replace(",", "", $request->total_amount);
    $duration           = (int) $request->duration;
    $userToken          = $request->user_token;
    $date_created       = \Carbon\Carbon::now('CAT');
    $expiry_date        = \Carbon\Carbon::now('CAT')->addMonths($duration);

    // 4. Payment system integration
    $pawapayResponse = ['error' => false]; // default safe state
    $payment_successful = true;

    // TNM Mpamba or Airtel Money
    if ($payment_method == 100 || $payment_method == 101) {
        // Generate a UUIDv4 based ID for aggregator
        $uuid = \Str::uuid()->toString();
        $transaction_id = $uuid;

        $mno = $payment_method == 100 ? Payment::TNM_MNO : Payment::AIRTEL_MNO;
        $pawapayResponse = $this->payPawaPay($transaction_id, $user, $total_amount_fees, $mno, $desc, $userToken);
        $payment_successful = false; // Always pending until webhook
    }

    // 5. Check for aggregator errors
    if ($pawapayResponse['error'] == true) {
        \Log::error('PawaPay error:', $pawapayResponse);
        return response()->json([
            'error_type' => "PawaPay",
            'error_status' => $pawapayResponse["error_status"],
            'error_message' => $pawapayResponse["error_message"]
        ], 500);
    }

    // 6. Create the payment record
    $payment = new Payment;
    $payment->client = $client;
    $payment->transaction_id = $transaction_id;
    $payment->insurance_type = $insurance_type;
    $payment->livestock_type = $livestock_type;
    $payment->livestock_quantity = $livestock_quantity;
    $payment->duration = $duration;
    $payment->total_amount = $total_amount_fees;
    $payment->payment_method = $payment_method;
    $payment->date_paid = $date_created;
    $payment->expiry_date = $expiry_date;
    $payment->paid_by = $user;

    // 7. Set status: 1 = paid, 2 = pending, 0 = failed
    if ($payment_successful) {
        $payment->status = 1;
    } else {
        $payment->status = 2; // pending
    }

    $payment->save();

    // 8. Return response
    return response()->json([
        'error' => false,
        'transaction_id' => $transaction_id,
        'transaction_time' => $date_created,
        'status' => $payment->status,
        'message' => $payment->status == 2 ? 'Pending payment' : 'Payment processed'
    ]);
}
    public function PayFees(Request $request)
    {

        //      return $request->livestock_type.'------'.$request['livestock_type'];
        // 1) Give ID to transaction
        do {
            $transaction_id = strtoupper($this->unique_code());
        } while (DB::table('payments')
            ->where('transaction_id', $transaction_id)->exists()
        );



        $payment_successful = true;

        $user = $request->user;
        $client = $request->phone_number;

        $insurance_type = $request->insurance_type;
        $livestock_quantity = $request->livestock_quantity;


        //Log::error("=========".$request->insurance_type."===".$request->livestock_type."===".$request->payment_method);

        if (InsuranceType::where('description', $request->insurance_type)->first()) {
            $insurance_type = InsuranceType::where('description', $request->insurance_type)->first()->id;
        } else {

            $insurance_type = $request->insurance_type;
        }
        $livestock_type = LivestockType::where('description', $request->livestock_type)->first()->id;
        $payment_method = PaymentMethod::where('description', $request->payment_method)->first()->code;


        $desc = "qty " . $livestock_quantity . " ls " . $livestock_type;

        $total_amount_fees  = str_replace(",", "", $request->total_amount);
        $duration           = (int) $request->duration;
        $userToken          = $request->user_token;
        $date_created       = \Carbon\Carbon::now('CAT');
        $expiry_date        = \Carbon\Carbon::now('CAT')->addMonths($duration);

        // Payment system integration

        $pawapayResponse = ['error' => false]; // default safe state
        // TNM Mpamba
        if ($payment_method == 100) {
            // Generate a UUIDv4 based ID
            $uuid = Uuid::uuid4();

            // Convert it to a string
            $uuidString = $uuid->toString();
            $transaction_id = $uuidString;

            $pawapayResponse = $this->payPawaPay($transaction_id, $user, $total_amount_fees, Payment::TNM_MNO, $desc, $userToken);
            $payment_successful = false;
        }

        // Airtel Money
        if ($payment_method == 101) {


            // Generate a UUIDv4 based ID
            $uuid = Uuid::uuid4();

            // Convert it to a string
            $uuidString = $uuid->toString();
            $transaction_id = $uuidString;

            $pawapayResponse = $this->payPawaPay($transaction_id, $user, $total_amount_fees, Payment::AIRTEL_MNO, $desc, $userToken);
            $payment_successful = false;
        }


        // Check if there was a problem making the pawapay request
        if ($pawapayResponse['error'] == true) {
            return response()->json([
                'error_type' => "PawaPay",
                'error_status' => $pawapayResponse["error_status"],
                'error_message' => $pawapayResponse["error_message"]
            ], 500);
        }



        $results = array();
        try {

            /*ToDo
             *
             * Integrate payment systems
             *
             */



            $payment = new Payment;
            $payment->client = $client;
            $payment->transaction_id = $transaction_id;
            $payment->insurance_type = $insurance_type;
            $payment->livestock_type = $livestock_type;
            $payment->livestock_quantity = $livestock_quantity;
            $payment->duration = $duration;
            $payment->total_amount = $total_amount_fees;
            $payment->payment_method = $payment_method;
            $payment->date_paid = $date_created;
            $payment->expiry_date = $expiry_date;

            $payment->paid_by = $user;

            if ($payment_successful) {
                $payment->status = 1;
                $results["error"] = false;
                $results["transaction_id"] = $transaction_id;
                $results["transaction_time"] = $date_created;
                $results["status"] = 1;
            } else {
                $payment->status = 2;
                $results["error"] = false;
                $results["transaction_id"] = $transaction_id;
                $results["transaction_time"] = $date_created;
                $results["status"] = 2;
            }

            // pawapay payments rendering 3 for "processing"
            if ($payment_method == 101 || $payment_method == 100) {
                $results["status"] = 3;
                $results["error"] = false;
                $results["transaction_id"] = $transaction_id;
                $results["transaction_time"] = $date_created;
                $results["message"] = "Pending payment";
            }
        } catch (Exception $exception) {
            $payment->status = 0;
            $results["error"] = true;
            $results["transaction_id"] = $payment->transaction_id;
            $results["transaction_time"] = $date_created;
            $results["status"] = 0;
        }

        $payment->save();
        return $results['status'];
    }

    private function payPawaPay($transId, $payer, $amount, $mno, $desc, $userToken)
    {

        $mnoStatus = $this->checkMNOStatus($mno);


        // When the MNO requested is down, cancel operation and notify user to try again later.
        if ($mnoStatus != "OPERATIONAL") {
            return [
                'error' => true,
                "error_status" => "MNO_UNAVAILABLE",
                "error_message" => $mnoStatus,
            ];
        }

        $sandbox = "https://api.sandbox.pawapay.cloud/";
        $production = "https://api.pawapay.cloud/";
        $deposit = $production . "deposits";
        $currentTimestamp = (new DateTime())->format(DateTime::RFC3339);


        $apiKeySandbox = 'eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJqdGkiOiJjNjI2Nzk0Ny0xNzdlLTRlNDUtYmM0Zi1mOWE2OTYzNDI2OWQiLCJzdWIiOiIyOTYiLCJpYXQiOjE2OTYzMjg5NDYsImV4cCI6MjAxMTk0ODE0NiwicG0iOiJEQUYsUEFGIiwidHQiOiJBQVQifQ.aEGjs0legiO4S7mGO90hLgjxwk0SIENDCXEhydP1tMw';
        $apiKey = 'eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJqdGkiOiIwMzIzNWFmOS1lODA0LTQwNTMtYTI0Yy03Y2NmZDkxODE4YTIiLCJzdWIiOiIzMTciLCJpYXQiOjE2OTYzMjg2OTMsImV4cCI6MjAxMTk0Nzg5MywicG0iOiJEQUYsUEFGIiwidHQiOiJBQVQifQ.lXGqVT0Z2jew1nqP6jrv_Y8s7XUCDCclJS3wzRBtSbg';

        $response = Http::withHeaders([
            'Authorization' => "Bearer " . $apiKey,
            'Content-Type' => 'application/json',
        ])->post($deposit, [
            "depositId" => $transId,
            "amount" => $amount,
            "currency" => "MWK",
            "country" => "MWI",
            "correspondent" => $mno,
            "payer" => [
                "type" => "MSISDN",
                "address" => [
                    "value" => $payer,
                ],
            ],
            "customerTimestamp" => $currentTimestamp,
            "statementDescription" => $desc,
            // "preAuthorisationCode" => "QJS3RSKZXY",
        ]);

        $status = $response['status'];

        // dd($status);
        if ($status == "REJECTED" || $status == "DUPLICATE_IGNORED") {
            return [
                'error' => true,
                "error_status" => $status,
                "error_message" => $response['rejectionReason']['rejectionMessage'],
            ];
        }


        try {
            $pawaPayment = new PawaPayment();
            $pawaPayment->transaction_id  = $response['depositId'];
            $pawaPayment->payer_msisdn          = $payer;
            $pawaPayment->amount          = $amount;
            $pawaPayment->description          = $desc;
            $pawaPayment->status = $response['status'];
            $pawaPayment->user_token = $userToken;

            $pawaPayment->save();
            //    dd($pawaPayment);

        } catch (Exception $exception) {
            // $output["error"] = true;
            // $output["message"] = "Some error";

        }



        return [
            'error' => false,
            'status' => $status,
            'data' => json_decode($response->body(), true),
        ];
    }

    public function pawaPayDepositsCallback(Request $request)
    {

        // save the full payload sent to us
        $endpoint = new EndPointResponse;
        $stringValue = json_encode($request->all());
        $endpoint->value = $stringValue;
        $endpoint->save();
        // return;



        $transactionId = $request['depositId'];
        $correspondent = $request['correspondent'];
        $financialTransactionId = isset($request['correspondentIds']['financialTransactionId']) ? $request['correspondentIds']['financialTransactionId'] : null;
        $country = $request['country'];
        $created = $request['created'];
        $currency = $request['currency'];
        $customerTimestamp = $request['customerTimestamp'];
        $depositedAmount = $request['depositedAmount'];
        $type = $request['payer']['type'];
        $requestedAmount = $request['requestedAmount'];
        $statementDescription = $request['statementDescription'];
        $status = $request['status'];
        $failureCode = isset($request['failureReason']['failureCode']) ? $request['failureReason']['failureCode'] : null;;
        $failureMessage = isset($request['failureReason']['failureMessage']) ? $request['failureReason']['failureMessage'] : null;;


        // Additional processing code can go here

        $pawaPayment = PawaPayment::find($transactionId);

        $pawaPayment->type                          = $type;
        $pawaPayment->description                   = $statementDescription;
        $pawaPayment->status                        = $status;
        $pawaPayment->failure_code                  = $failureCode;
        $pawaPayment->failure_message               = $failureMessage;
        $pawaPayment->financial_trans_id            = $financialTransactionId;
        $pawaPayment->save();


        // update payment table to completed payment
        $payment = Payment::find($transactionId);
        $payment = Payment::where('transaction_id', '=', $transactionId)->first();

        if ($status == "COMPLETED") {
            $payment->status = 1;
        }


        $payment->save();

        //send reques$request to mobile application
        // return $pawaPayment;

        // dd($pawaPayment);
        $this->sendNotificationToUser($pawaPayment);

        return ["status" => "successful"];
    }

    public function checkPawaPayment(Request $request)
    {
        $sandbox = "https://api.sandbox.pawapay.cloud/";
        $production = "https://api.pawapay.cloud/";

        $depositId =  $request->depositId;
        $deposit = $production . "deposits/{$depositId}";




        $apiKey = 'eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJqdGkiOiIwMzIzNWFmOS1lODA0LTQwNTMtYTI0Yy03Y2NmZDkxODE4YTIiLCJzdWIiOiIzMTciLCJpYXQiOjE2OTYzMjg2OTMsImV4cCI6MjAxMTk0Nzg5MywicG0iOiJEQUYsUEFGIiwidHQiOiJBQVQifQ.lXGqVT0Z2jew1nqP6jrv_Y8s7XUCDCclJS3wzRBtSbg';


        $response = Http::withHeaders([
            'Authorization' => "Bearer {$apiKey}",
            'Content-Type' => 'application/json',
        ])->get($deposit);



        return $response->json();
    }

    public function checkMNOStatus($mno)
    {

        try {
            $production = "https://api.pawapay.cloud/availability/";


            $apiKey = 'eyJraWQiOiIxIiwiYWxnIjoiSFMyNTYifQ.eyJqdGkiOiIwMzIzNWFmOS1lODA0LTQwNTMtYTI0Yy03Y2NmZDkxODE4YTIiLCJzdWIiOiIzMTciLCJpYXQiOjE2OTYzMjg2OTMsImV4cCI6MjAxMTk0Nzg5MywicG0iOiJEQUYsUEFGIiwidHQiOiJBQVQifQ.lXGqVT0Z2jew1nqP6jrv_Y8s7XUCDCclJS3wzRBtSbg';


            $response = Http::withHeaders([
                'Authorization' => "Bearer {$apiKey}",
                'Content-Type' => 'application/json',
            ])->get($production);



            $responseData = json_decode($response, true);

            // Find the entry with Malawi (country code: "MWI")
            $malawiEntry = null;
            foreach ($responseData as $entry) {
                if ($entry['country'] === 'MWI') {
                    $malawiEntry = $entry;
                    break;
                }
            }
            if ($malawiEntry) {
                $data = $malawiEntry;

                // Find the correspondent with the name "TNM_MWI" or "AIRTEL_MWI"
                $tnmMwiCorrespondent = null;
                foreach ($data['correspondents'] as $correspondent) {
                    if ($correspondent['correspondent'] === $mno) {
                        $tnmMwiCorrespondent = $correspondent;
                        break;
                    }
                }

                // Check if the correspondent  was found
                if ($tnmMwiCorrespondent) {
                    // Find the status of "DEPOSIT" operationType
                    $depositStatus = null;
                    foreach ($tnmMwiCorrespondent['operationTypes'] as $operationType) {
                        if ($operationType['operationType'] === 'DEPOSIT') {
                            $depositStatus = $operationType['status'];
                            break;
                        }
                    }

                    // Output the result
                    if ($depositStatus) {
                        return $depositStatus;
                    } else {
                        return "DEPOSIT operation type not found for {$mno}";
                    }
                } else {
                    return "Correspondent {$mno} not found";
                }
            } else {

                return "Country MWI not found";
            }
        } catch (Exception $error) {
            // Handle the error appropriately
            error_log('pawaPayAvailability - error: ' . $error->getMessage());
            return $error->getMessage();
        }
    }

    public function submitProposalUssd(Request $request)
    {
        try {
            error_log('Request Data: ' . json_encode($request->all()));

            $proposal = new InsuranceProposal;
            $proposal->customer = $request->phone_number;
            $proposal->created_by = $request->phone;
            $proposal->insurance_type = $request->insurance_type;
            $proposal->livestock_type = $request->livestock_type;
            // $proposal->status=1;

            if ($proposal->save()) {
                error_log('Proposal saved successfully.');
                return response()->json(['message' => 'Proposal submitted successfully!','data' => $proposal,], 200);
            } else {
                error_log('Failed to save proposal.');
                return response()->json(['error' => 'Failed to save proposal.',], 500);
            }
        } catch (Exception $e) {
            error_log('Error submitting proposal: ' . $e->getMessage());
            error_log('Stack trace: ' . $e->getTraceAsString());

            return response()->json([
                'error' => 'Failed to submit proposal. Please try again later.',
                'details' => $e->getMessage(), 
            ], 500);
        }
    }

    function sendNotificationToUser($pawaPayment)
    {
        return FCMService::send(
            $pawaPayment->user_token,
            [
                'Pawa Payment' => $pawaPayment,
            ],
            "test notification"
        );
    }

    function unique_code()
    {
        return substr(base_convert(sha1(uniqid(mt_rand())), 16, 36), 0, 16);
    }


    public function pawapayWebhook(Request $request)
{
 
    $transaction_id = $request->input('depositId');
    $status = $request->input('status'); 

    $payment = Payment::where('transaction_id', $transaction_id)->first();

    if ($payment) {
        if ($status === 'SUCCESSFUL') {
            $payment->status = 1; 
        } else {
            $payment->status = 0; 
        }
        $payment->save();

        return response()->json(['success' => true]);
    } else {
        return response()->json(['success' => false, 'error' => 'Payment not found'], 404);
    }
}


public function initiateVetCall(Request $request)
{
    $request->validate([
        'phone_number' => ['required'],
        'vet_username' => ['nullable', 'string'],
    ]);

    $farmer = Client::where('phone_number', $request->phone_number)->first();
    if (!$farmer) {
        return response()->json(['error' => 'Farmer not found'], 404);
    }

    if (!$request->filled('vet_username')) {
        $vets = User::where('user_type', '1')
            ->where('institution', $farmer->epa)
            ->get();

        if ($vets->isEmpty()) {
            return response()->json(['error' => 'No vet found for your institution'], 404);
        }

        $vetList = $vets->map(function ($vet) {
            return [
                'name' => $vet->name,
                'username' => $vet->username,
            ];
        });

        return response()->json(['vets' => $vetList], 200);
    }

    $vet = User::where('user_type', '1')
        ->where('institution', $farmer->epa)
        ->where('username', $request->vet_username)
        ->first();

    if (!$vet) {
        return response()->json(['error' => 'Selected vet not found'], 404);
    }

    $message = sprintf(
        "Hello %s, farmer %s (%s) from %s needs your assistance.",
        $vet->name,
        $farmer->name ?? 'Unknown',
        $farmer->phone_number,
        $farmer->epa
    );

    $output = [
        'phone_number' => $vet->phone_number,
    ];

    $notifyResult = $this->NotifyClient($output, $message);

    if (!$notifyResult) {
        return response()->json(['error' => 'Failed to notify vet'], 500);
    }

    return response()->json([
        'message' => 'Vet notified successfully',
        'vet' => [
            'name' => $vet->name,
            'username' => $vet->username,
        ],
    ], 200);
}

}   
