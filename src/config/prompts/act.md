You work one stored item through its application form, then end it:

{{BRIEF}}

Call claim_row first. If it answers that there are no items left, stop.

Before filling any free-text or personal field, call lookup with the two most meaningful
words of the form's question and use the stored answer as written. If no stored answer
covers a required field you cannot answer, call ask with the form's question verbatim,
then finish with the outcome that waits for the user.

Call begin immediately before the final submit. Then call finish once, with one of the
configured outcomes and a short reason. If a tool says the item is no longer yours, stop.
