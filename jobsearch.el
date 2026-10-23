;;; jobsearch.el --- Job-search pipeline in Org  -*- lexical-binding: t; -*-

;;; Commentary:

;; Loaded from .dir-locals.el.  ONE entry per company; the TODO state is the
;; pipeline stage:  LEAD -> APPLIED -> INTERVIEW -> OFFER -> HIRED/REJECTED.
;; HOLD = waiting until a form question has an answer in
;; application-questions.org.
;;
;; `cv-jobs-lead-fields' is the only description of a LEAD.  Everything goes
;; through `cv-jobs-lead', and the rest are thin wrappers:
;;
;;   (cv-jobs-add "Acme" "Backend Engineer" :url "https://acme.dev/jobs")
;;   (cv-jobs-get "Acme")
;;   (cv-jobs-update "Acme" :state "APPLIED" :note "Application sent.")
;;   (cv-jobs-archive "Acme")
;;
;; gptel: the `job' supervisor delegates to `job-search' / `job-apply', which
;; live in agents/*.org.  `job-apply' sends the user's own application email
;; when the lead offers an address, and only otherwise drives the browser.

;;; Code:

(require 'org)
(require 'org-capture)
(require 'org-agenda)
(require 'subr-x)
(require 'seq)
(require 'sendmail)
(require 'message)
(require 'mml)
(require 'gptel)
(require 'mcp)
(require 'gptel-integrations)
(require 'gptel-agent nil t)

(setq-default gptel-confirm-tool-calls nil)
;; Modern Org no longer installs the global entry points.
(dolist (k '("C-c c" "C-c a"))
  (unless (lookup-key global-map (kbd k))
    (global-set-key (kbd k) (if (equal k "C-c c") #'org-capture #'org-agenda))))

(defconst cv-jobs-directory
  (file-name-directory (or load-file-name
                           (locate-dominating-file default-directory ".dir-locals.el")
                           default-directory))
  "Root of the CV / job-search project.")

(defconst cv-jobs-file (expand-file-name "jobs.org" cv-jobs-directory)
  "The Org tracker file for the job search.")

(defcustom cv-jobs-follow-up-days 14
  "Days until the follow-up when a lead becomes APPLIED."
  :type 'integer)

(defcustom cv-jobs-email-template-file
  (expand-file-name "application-email-template.txt" cv-jobs-directory)
  "Your application email: a `Subject: ...' first line, body after it.
Only {{position}} and {{company}} are substituted."
  :type 'file)

(defcustom cv-jobs-email-attachments
  (list (expand-file-name "result/Ivan_Dimitrov_Resume_IT_Consultant.pdf" cv-jobs-directory))
  "Files attached to application emails."
  :type '(repeat file))

;;; The lead template

(defconst cv-jobs-lead-fields
  '((company . "Company")
    (role . "Role (position title)")
    (url . "URL")
    (location . "Location|Remote|Da Nang|Other")
    (size . "Size|small|mid|large")
    (english . "English-speaking?|yes|unknown|no")
    (contact . "Contact / application email"))
  "The shape of a LEAD: field . capture prompt.
Company and role also form the heading; every field is a property on the
entry.  This list drives capture, creation, reading and the gptel tools.")

(defun cv-jobs--prop (field)
  "Return the Org property name for FIELD."
  (upcase (symbol-name field)))

(defun cv-jobs--format (lead)
  "Format LEAD as a short report for the model."
  (mapconcat (lambda (k)
               (format "%s: %s" (capitalize (substring (symbol-name k) 1))
                       (or (plist-get lead k) "-")))
             (append '(:heading :state) (mapcar #'car cv-jobs-lead-fields))
             "\n"))

(defun cv-jobs--lead-at (selector)
  "Return a marker for SELECTOR: a heading substring, a marker, or nil.
Nil means the entry at point in jobs.org, else the first LEAD."
  (cond
   ((markerp selector) selector)
   ((stringp selector)
    (with-current-buffer (find-file-noselect cv-jobs-file)
      (car (org-map-entries
            (lambda () (and (string-match-p (regexp-quote selector)
                                            (org-get-heading t t t t))
                            (point-marker)))
            nil 'file))))
   ((and (derived-mode-p 'org-mode) buffer-file-name
         (file-equal-p buffer-file-name cv-jobs-file))
    (save-excursion (org-back-to-heading t) (point-marker)))
   (t (with-current-buffer (find-file-noselect cv-jobs-file)
        (save-excursion
          (goto-char (point-min))
          (when (re-search-forward "^\\*+ Pipeline" nil t)
            (org-back-to-heading t)
            (car (org-map-entries #'point-marker "+TODO=\"LEAD\"" 'tree))))))))

(defun cv-jobs--lead-read (selector)
  "Return the lead at SELECTOR as a plist, or nil."
  (when-let* ((m (cv-jobs--lead-at selector)))
    (with-current-buffer (marker-buffer m)
      (save-excursion
        (goto-char m)
        (append (list :heading (org-get-heading t t t t)
                      :state (org-get-todo-state))
                (mapcan (lambda (f)
                          (list (car f) (org-entry-get nil (cv-jobs--prop (car f)))))
                        cv-jobs-lead-fields))))))

(defun cv-jobs--note (text)
  "Append TEXT to the entry at point as a timestamped note."
  (save-excursion
    (org-back-to-heading t)
    (org-end-of-subtree t)
    (unless (bolp) (insert "\n"))
    (insert "- Note " (format-time-string "[%Y-%m-%d %a %H:%M]") " " text "\n")))

(defun cv-jobs--lead-insert (lead)
  "Insert LEAD at the end of the Pipeline tree; return its marker."
  (with-current-buffer (find-file-noselect cv-jobs-file)
    (save-excursion
      (goto-char (point-min))
      (unless (re-search-forward "^\\*+ Pipeline" nil t)
        (user-error "No Pipeline tree in %s" cv-jobs-file))
      (org-end-of-subtree t)
      (unless (bolp) (insert "\n"))
      (let ((start (point)))
        (insert "* " (or (plist-get lead :state) "LEAD") " "
                (plist-get lead :company) " — " (plist-get lead :role) "\n"
                ":PROPERTIES:\n"
                (mapconcat (lambda (f)
                             (if-let* ((v (plist-get lead (car f))))
                                 (format ":%s: %s\n" (cv-jobs--prop (car f)) v)
                               ""))
                           cv-jobs-lead-fields "")
                ":END:\n")
        (save-buffer)
        (copy-marker start)))))

(defun cv-jobs--lead-put (marker fields)
  "Write FIELDS to the entry at MARKER and return the updated lead."
  (with-current-buffer (marker-buffer marker)
    (save-excursion
      (goto-char marker)
      (dolist (f cv-jobs-lead-fields)
        (when-let* ((v (plist-get fields (car f))))
          (org-entry-put nil (cv-jobs--prop (car f)) v)))
      (when-let* ((state (plist-get fields :state)))
        (org-todo state))
      (when (equal (org-get-todo-state) "APPLIED")
        (org-entry-put nil "APPLIED_ON" (format-time-string "%Y-%m-%d"))
        (org-schedule nil (format-time-string
                           "%Y-%m-%d"
                           (time-add (current-time)
                                     (days-to-time cv-jobs-follow-up-days)))))
      (when-let* ((note (plist-get fields :note)))
        (cv-jobs--note note)))
    (save-buffer))
  (cv-jobs--lead-read marker))

(defun cv-jobs-lead (selector &rest fields)
  "The single interface to the job pipeline.

SELECTOR is a marker, a substring of a heading (e.g. the company), or nil
(the entry at point in jobs.org, else the first LEAD).

With no FIELDS, return the lead as a plist.  With FIELDS -- any key from
`cv-jobs-lead-fields' plus :state and :note -- update that lead, or create
it when nothing matches and :company and :role are given."
  (if (null fields)
      (cv-jobs--lead-read selector)
    (let ((m (and selector (cv-jobs--lead-at selector))))
      (unless m
        (unless (and (plist-get fields :company) (plist-get fields :role))
          (user-error "Creating a lead needs :company and :role"))
        (setq m (cv-jobs--lead-insert fields)))
      (cv-jobs--lead-put m fields))))

;;; CRUD

(defun cv-jobs-add (company role &rest fields)
  "Create a LEAD for COMPANY applying to ROLE, plus optional FIELDS."
  (apply #'cv-jobs-lead nil :company company :role role fields))

(defun cv-jobs-get (selector)
  "Return the lead at SELECTOR as a plist."
  (cv-jobs-lead selector))

(defun cv-jobs-update (selector &rest fields)
  "Update the lead at SELECTOR with FIELDS."
  (apply #'cv-jobs-lead selector fields))

(defun cv-jobs--archive (marker)
  "Archive the entry at MARKER into the file's Archive tree."
  (with-current-buffer (marker-buffer marker)
    (save-excursion
      (goto-char marker)
      (let ((org-archive-location (concat cv-jobs-file "::* Archive")))
        (org-archive-subtree)))
    (save-buffer)))

(defun cv-jobs-archive (&optional selector)
  "Archive the lead at SELECTOR (default: the entry at point)."
  (interactive)
  (if-let* ((m (cv-jobs--lead-at selector)))
      (cv-jobs--archive m)
    (user-error "No matching lead")))

(defun cv-jobs-apply ()
  "Mark the lead at point APPLIED and log it."
  (interactive)
  (unless (derived-mode-p 'org-mode) (user-error "Not in an Org buffer"))
  (cv-jobs-update (point-marker) :state "APPLIED" :note "Application sent."))

;;; Capture and agenda

(defvar cv-jobs--captures nil
  "Capture entries installed by this file.")

(defun cv-jobs--capture-entry ()
  "Return the capture template text generated from `cv-jobs-lead-fields'."
  (concat "* LEAD %^{Company} — %^{Role}\n"
          ":PROPERTIES:\n"
          ":COMPANY: %\\1\n"
          ":ROLE: %\\2\n"
          ;; Note: build the `%^{prompt}' escape with concat, not as part of
          ;; a `format' string -- `format' would choke on the literal `%^'.
          (mapconcat (lambda (f)
                       (format ":%s: %s\n" (cv-jobs--prop (car f))
                               (concat "%^{" (cdr f) "}")))
                     (seq-drop cv-jobs-lead-fields 2) "")
          ":END:\n%?"))

(defun cv-jobs--setup-captures ()
  "Install the job capture templates, idempotently."
  (setq org-capture-templates
        (append (seq-remove (lambda (e) (member e cv-jobs--captures))
                            org-capture-templates)
                (setq cv-jobs--captures
                      (list '("j" "Job search")
                            (list "jl" "New lead" 'entry
                                  (list 'file+headline cv-jobs-file "Pipeline")
                                  (cv-jobs--capture-entry)))))))

(defun cv-jobs--setup-agenda ()
  "Add jobs.org to the agenda and install the pipeline view."
  (add-to-list 'org-agenda-files cv-jobs-file)
  (add-to-list 'org-agenda-custom-commands
               '("j" "Job pipeline"
                 ((tags-todo "+TODO=\"LEAD\"" ((org-agenda-overriding-header "Leads")))
                  (tags-todo "+TODO=\"APPLIED\"" ((org-agenda-overriding-header "Applied")))
                  (tags-todo "+TODO=\"INTERVIEW\"" ((org-agenda-overriding-header "Interviewing")))
                  (tags-todo "+TODO=\"HOLD\"" ((org-agenda-overriding-header "Waiting for answers")))
                  (agenda "" ((org-agenda-span 14)))))))

(defun cv-jobs-agenda ()
  "Open the job-search agenda pipeline."
  (interactive)
  (org-agenda nil "j"))

(defun cv-jobs--setup-buffer ()
  "Local keys and archive location for jobs.org."
  (when (and buffer-file-name (file-equal-p buffer-file-name cv-jobs-file))
    (local-set-key (kbd "C-c j a") #'cv-jobs-agenda)
    (local-set-key (kbd "C-c j A") #'cv-jobs-apply)
    (local-set-key (kbd "C-c j x") #'cv-jobs-archive)
    (setq-local org-archive-location (concat cv-jobs-file "::* Archive"))))

;;; Application email

(defvar cv-jobs--send-step "start"
  "Last step attempted by `cv-jobs-send-application-email'.")

(defun cv-jobs--fill (text lead)
  "Substitute {{position}} and {{company}} in TEXT from LEAD."
  (replace-regexp-in-string
   "{{\\([a-z]+\\)}}"
   (lambda (m)
     (let ((key (intern (concat ":" (substring m 2 -2)))))
       (unless (memq key '(:position :company))
         (user-error "Unsupported placeholder %s in the email template" m))
       (or (plist-get lead key) "")))
   text t t))

(defun cv-jobs--mail-buffer (email subject body)
  "Return an unsent `mail-mode' buffer with EMAIL, SUBJECT and BODY.
The caller attaches the CV files and sends it, then kills the buffer."
  (let ((buffer (generate-new-buffer "*job application*")))
    (with-current-buffer buffer
      (insert "To: " email "\n"
              "Subject: " subject "\n"
              mail-header-separator "\n"
              body)
      (unless (bolp) (insert "\n"))
      (mail-mode)
      buffer)))

(defun cv-jobs-send-application-email (email company position)
  "Send the user's pre-written application email to EMAIL.
COMPANY and POSITION are the only values substituted into
`cv-jobs-email-template-file'; the body is never generated here, and the
files in `cv-jobs-email-attachments' are attached."
  (interactive "sApplication email: \nsCompany: \nsPosition: ")
  (let ((cv-jobs--send-step "reading the template")
        buffer)
    (condition-case err
        (progn
          (unless (string-match-p "@" email)
            (user-error "Not an email address: %s" email))
          (let ((text (with-temp-buffer
                        (insert-file-contents cv-jobs-email-template-file)
                        (buffer-string))))
            (when (string-match-p "<<<" text)
              (user-error "Write your application email in %s first"
                          cv-jobs-email-template-file))
            (setq cv-jobs--send-step "parsing the template")
            (unless (string-match "\\`Subject:[ \t]*\\(.+\\)\n" text)
              (user-error "The first line of %s must be `Subject: ...'"
                          cv-jobs-email-template-file))
            (let* ((lead (list :company company :position position))
                   (subject (cv-jobs--fill (match-string 1 text) lead))
                   (body (cv-jobs--fill (substring text (match-end 0)) lead))
                   (attachments (seq-filter #'identity cv-jobs-email-attachments)))
              (dolist (file attachments)
                (unless (file-readable-p file)
                  (user-error "Attachment %s is missing" file)))
              (when (and attachments (not (fboundp 'mail-add-attachment)))
                (user-error "Cannot attach %s: this Emacs has no `mail-add-attachment'"
                            (car attachments)))
              (setq cv-jobs--send-step "composing the message")
              (setq buffer (cv-jobs--mail-buffer email subject body))
              (with-current-buffer buffer
                (setq cv-jobs--send-step "attaching the CV")
                (dolist (file attachments)
                  (mail-add-attachment file))
                (setq cv-jobs--send-step "sending")
                ;; `send-mail-function' is sendmail-send-it here, so this
                ;; hands the message to msmtp and waits for the exit status.
                (let ((mail-interactive t))
                  (mail-send)))
              (when (buffer-live-p buffer) (kill-buffer buffer))
              (format "Application email sent to %s for %s at %s."
                      email position company))))
      (error
       (when (buffer-live-p buffer) (kill-buffer buffer))
       (format "Application email NOT sent (%s): %s"
               cv-jobs--send-step (error-message-string err))))))

;;; Application answers

(defconst cv-jobs-answers-file
  (expand-file-name "application-questions.org" cv-jobs-directory)
  "Org file of generic application-question answers, one per entry.
The question is the heading; the answer is the :ANSWER: property.")

(defcustom cv-jobs-answers-limit 3
  "How many answers `cv-jobs-find-answers' returns at most.
The best matches come first; nil means no limit."
  :type '(choice (integer :tag "Answers") (const :tag "No limit" nil)))

(defun cv-jobs-find-answers (&optional query)
  "Return the stored answers to application questions matching QUERY.

QUERY is matched against the question headings with Emacs' `orderless'
completion style, so case and word order do not matter; nil or an
empty string returns every answer.
The result is plain text: numbered questions, each followed by
its answer on a \"- \" line, ready to hand to the model as is."
  (with-current-buffer (find-file-noselect cv-jobs-answers-file)
    (let* ((completion-styles '(orderless))
           (completion-ignore-case t)
           (answers (org-map-entries
                     (lambda ()
                       (when-let* ((answer (org-entry-get nil "ANSWER")))
                         (cons (org-get-heading t t t t) answer)))
                     nil 'file))
           (matches (completion-all-completions (or query "")
                                                (mapcar #'car answers) nil 0)))
      ;; The result's last cdr is a base size, not an element.
      (when (integerp (cdr (last matches))) (setcdr (last matches) nil))
      (mapconcat #'identity
                 (seq-map-indexed (lambda (m i)
                                    (let ((q (substring-no-properties m)))
                                      (format "%d. %s\n- %s" (1+ i) q
                                              (cdr (assoc q answers)))))
                                  (seq-take matches cv-jobs-answers-limit))
                 "\n"))))

;;; Agent tools

(defvar cv-jobs--current nil
  "Marker of the lead most recently handed to the agent.")

(defun cv-jobs-next-lead ()
  "Report the first LEAD in the Pipeline and remember it for the next tools."
  (interactive)
  (with-current-buffer (find-file-noselect cv-jobs-file)
    (setq cv-jobs--current
          (save-excursion
            (goto-char (point-min))
            (when (re-search-forward "^\\*+ Pipeline" nil t)
              (org-back-to-heading t)
              (car (org-map-entries #'point-marker "+TODO=\"LEAD\"" 'tree))))))
  (if cv-jobs--current
      (cv-jobs--format (cv-jobs--lead-read cv-jobs--current))
    "No LEAD entries left in the pipeline."))

(defun cv-jobs-mark-applied ()
  "Mark the remembered lead APPLIED, scheduling the follow-up."
  (interactive)
  (if (not (and cv-jobs--current (marker-buffer cv-jobs--current)))
      "No lead selected; call cv_jobs_next_lead first."
    (let ((heading (plist-get (cv-jobs-lead cv-jobs--current
                                            :state "APPLIED"
                                            :note "Application sent.")
                              :heading)))
      (setq cv-jobs--current nil)
      (format "Marked %s as APPLIED and scheduled a follow-up." heading))))

(defun cv-jobs-hold-for-question (question)
  "Put the remembered lead on HOLD until QUESTION has an answer.
The question is added to `cv-jobs-answers-file' without an :ANSWER:
property, so `cv-jobs-find-answers' keeps ignoring it."
  (interactive "sQuestion: ")
  (if (not (and cv-jobs--current (marker-buffer cv-jobs--current)))
      "No lead selected; call cv_jobs_next_lead first."
    (let* ((company (plist-get (cv-jobs-lead cv-jobs--current) :company))
           (answers-file (file-name-nondirectory cv-jobs-answers-file)))
      (with-current-buffer (find-file-noselect cv-jobs-answers-file)
        (unless (org-find-exact-headline-in-buffer question)
          (goto-char (point-min))
          (unless (re-search-forward "^\\*+ Recurring answers" nil t)
            (user-error "No Recurring answers tree in %s" answers-file))
          (org-end-of-subtree t)
          (unless (bolp) (insert "\n"))
          (insert "** " question "\n:PROPERTIES:\n:SOURCE: " company
                  "\n:END:\n")
          (save-buffer)))
      (cv-jobs-lead cv-jobs--current :state "HOLD"
                    :note (format "On hold, no answer for: %s" question))
      (setq cv-jobs--current nil)
      (format "Put %s on hold and saved the question to %s. Answer it there and set the lead back to LEAD."
              company answers-file))))

(defun cv-jobs-archive-lead (&optional reason)
  "Archive the remembered lead, logging REASON as a note."
  (interactive "sReason: ")
  (if (not (and cv-jobs--current (marker-buffer cv-jobs--current)))
      "No lead selected; call cv_jobs_next_lead first."
    (let* ((reason (or reason "Application abandoned."))
           (heading (plist-get (cv-jobs-lead cv-jobs--current :note reason) :heading)))
      (cv-jobs--archive cv-jobs--current)
      (setq cv-jobs--current nil)
      (format "Archived %s: %s" heading reason))))

(gptel-make-tool
 :name "cv_jobs_next_lead"
 :function #'cv-jobs-next-lead
 :description "Return the next LEAD from jobs.org with its state, url, location, size, English requirement and contact. Call this first to choose the company to apply to."
 :args nil
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_send_application_email"
 :function #'cv-jobs-send-application-email
 :description "Send the user's pre-written application email to a job-application address found on the lead's site. Only the position and company are filled in; never write or alter the body. Prefer this over an application form."
 :args '((:name "email" :type string
          :description "Application address from the site, e.g. careers@company.com.")
         (:name "company" :type string
          :description "Company name as it should appear in the email.")
         (:name "position" :type string
          :description "Exact title of the position applied for."))
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_find_answers"
 :function #'cv-jobs-find-answers
 :description "Look up the user's pre-written answers to application questions. Call this before filling any required free-text field. Pass the 2 most meaningful words of the form's question, not its full wording, e.g. \"salary expectation\" or \"notice period\": the stored questions are worded differently, so keywords match where sentences do not. The result is plain text: numbered questions, each followed by its answer. Use the answer as written, substituting placeholders like <Company> or <N> days. If nothing matches, do not invent an answer: call cv_jobs_hold_for_question instead."
 :args '((:name "query" :type string
          :description "The 2 keywords that best capture the question, e.g. \"salary expectation\"."))
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_hold_for_question"
 :function #'cv-jobs-hold-for-question
 :description "Put the lead from cv_jobs_next_lead on hold when a required free-text field asks something cv_jobs_find_answers has no answer for. Pass the question verbatim. The question is saved in application-questions.org for the user to answer later, and the lead leaves the pipeline until then. Never invent an answer."
 :args '((:name "question" :type string
          :description "The form's question, exactly as worded."))
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_mark_applied"
 :function #'cv-jobs-mark-applied
 :description "Mark the lead from cv_jobs_next_lead as APPLIED and schedule the follow-up. Call once the application has been submitted."
 :args nil
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_archive_lead"
 :function #'cv-jobs-archive-lead
 :description "Archive the lead from cv_jobs_next_lead with a reason, when the application cannot be completed. Use this instead of cv_jobs_mark_applied."
 :args '((:name "reason" :type string :optional t
          :description "Why the application was abandoned."))
 :category "jobsearch")

;;; gptel agents

(setq gptel-agent-dirs (list (expand-file-name "agents" cv-jobs-directory)))

(defun cv-jobs--connect-playwright ()
  "Start the Playwright MCP server used by the `job-apply' agent."
  (add-to-list 'mcp-hub-servers
               '("playwright" . (:command "playwright-mcp"
                                :args ("--executable-path"
                                       "/home/ivand/src/cv/.devenv/profile/bin/chromium-sandboxed"))))
  (gptel-mcp-connect '("playwright") t))

(defun cv-jobs--install-subagents ()
  "Register agents/*.org as sub-agents and as presets."
  (if (not (fboundp 'gptel-agent-update))
      (message "cv-jobs: gptel-agent is not available")
    (gptel-agent-update)
    ;; gptel-agent registers every file in the directory, undo-tree
    ;; droppings included; keep only the agents this project ships.
    (setq gptel-agent--agents
          (seq-filter (lambda (a) (member (car a) '("job-search" "job-apply")))
                      gptel-agent--agents))
    (setf (plist-get (car (gptel-tool-args (gptel-get-tool "Agent"))) :enum)
          (vconcat (mapcar #'car gptel-agent--agents)))
    (dolist (agent gptel-agent--agents)
      (apply #'gptel-make-preset (intern (car agent)) (cdr agent)))))

(gptel-make-preset 'job
  :description "Job-search supervisor; delegates to sub-agents."
  :backend "Deepseek"
  :model 'deepseek-v4-flash
  :use-tools t
  :tools '("Agent")
  :confirm-tool-calls nil
  :system
  (lambda ()
    (format "You are the job-search supervisor for ~/src/cv.

Delegate with the `Agent' tool; sub-agents see only your prompt, so briefs
must be self-contained.  `job-apply' applies to the next lead (by email when
the site offers an address, otherwise the form) and owns the cv_jobs_* tools.
`job-search' finds new leads.  Never edit jobs.org or invent form answers.
Report what the sub-agent returned.

Sub-agents:
%s"
            (mapconcat (lambda (a) (format "- %s: %s" (car a)
                                           (plist-get (cdr a) :description)))
                       (bound-and-true-p gptel-agent--agents) "\n"))))

;;; Setup

(defun cv-jobs-reload ()
  "Reload jobsearch.el so edits take effect without restarting Emacs."
  (interactive)
  (load (expand-file-name "jobsearch.el" cv-jobs-directory) nil nil)
  (message "Reloaded jobsearch.el"))

(defun cv-jobs-setup ()
  "Install captures, agenda, keys and the gptel agents; safe to re-run."
  (interactive)
  (cv-jobs--setup-captures)
  (cv-jobs--setup-agenda)
  (add-hook 'org-mode-hook #'cv-jobs--setup-buffer)
  (cv-jobs--setup-buffer)
  (cv-jobs--install-subagents))

(setq org-modern-todo-faces
      '(("LEAD" . "⟳") ("APPLIED" . "⇒") ("INTERVIEW" . "★")
        ("OFFER" . "★") ("HOLD" . "⏸") ("HIRED" . "✔") ("REJECTED" . "✘")))

(cv-jobs-setup)

(provide 'jobsearch)
;;; jobsearch.el ends here
