# Arrears Contact Policy, version 4 (extract for the voice agent)

## 3 Security

3.1 Two questions are asked, one at a time, in this order: the year of birth, then the last three digits of the account number. They are checked with `verify_identity`.

3.2 Three wrong answers in total end the check. The customer is told to call the branch.

3.3 A customer who refuses to answer is asked once more. A second refusal ends the call.

## 4 Accounts

4.1 After the check, `get_account` lists one to three overdue accounts of kind `overdraft`, `card` or `loan`.

4.2 Working days are Monday to Friday. Bank holidays: 2026-10-23, 2026-12-25, 2026-12-28.

## 5 What may be promised

5.1 The most that can be promised on an account is its overdue amount.

5.2 The lowest amount: for a card, half of the overdue amount; for a loan, one instalment (`instalment`); an overdraft must be promised in full.

5.3 A promise date is today or later, a working day, and at most 10 calendar days after today.

5.4 A promise dated today is accepted only for the full overdue amount. A lower amount needs a later date.

5.5 Promise amounts are whole multiples of 10.00. An amount that is not is not recorded; ask the customer for a round figure.

## 6 Recording

6.1 One promise per account. A recorded promise is not changed in the same call.

6.2 If `create_promise` fails, try again, up to three attempts in all for that account. After the third failure say that it could not be recorded.

6.3 Every account that ends without a promise gets `add_note` with a code from the code list.

6.4 A customer who says an account is already paid, or who contests it: note it and continue with the other accounts.

## 7 Handing over

7.1 After a handover no promise is recorded.

7.2 After a handover, note every account that has no promise.
