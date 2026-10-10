# Code list for arrears calls (current)

## Call results (`close_call`)

| Result | Reason code | Use |
|---|---|---|
| `agreed` | none | at least one promise was recorded and no handover followed |
| `settled` | none | no promise, and every account is noted `settled` |
| `handed_over` | `hardship`, `plan`, `requested` | the call was handed over. A handover wins over a recorded promise |
| `no_agreement` | `declined`, `contested`, `hardship`, `system_fault` | no promise and no handover: the code of the first account, in the order `get_account` gave them, whose note is not `settled` |
| `unverified` | `id_failed`, `id_refused` | the security check failed; the customer refused it |
| `no_contact` | `callback_set`, `no_slot`, `wrong_party` | a callback was booked; no callback time could be agreed; the number is not the customer's |

Every call ends with exactly one `close_call`.

## Teams (`transfer_to_agent`)

| Team | For | Reason code at close |
|---|---|---|
| `hardship_desk` | a customer in hardship who accepts the offer | `hardship` |
| `payment_plans` | a request for a payment plan | `plan` |
| `agent` | a customer who insists on a person | `requested` |

## Note codes (`add_note`)

| Code | Meaning |
|---|---|
| `declined` | the customer will not promise anything on this account |
| `contested` | the customer says the account is not theirs or the amount is wrong |
| `settled` | the customer says it is already paid |
| `hardship` | hardship, and the customer did not want the hardship desk |
| `system_fault` | the promise could not be recorded after three attempts |
