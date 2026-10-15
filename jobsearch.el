;;; jobsearch.el --- Project-local job-search workflow  -*- lexical-binding: t; -*-

;;; Commentary:

;; Loaded on demand from ~/src/cv/.dir-locals.el, so none of the job-search /
;; gptel glue lives in the main init file.
;;
;; Model: ONE entry per company.  The TODO state *is* the pipeline stage, so
;; nothing is duplicated between "lead" and "application" sections:
;;
;;   LEAD -> APPLIED -> INTERVIEW -> OFFER -> HIRED / REJECTED
;;
;; jobs.org holds a single "* Pipeline" tree; a "* Archive" tree keeps closed
;; companies out of the way.  Capture only ever creates a new LEAD; "applying"
;; is just a state change (`cv-jobs-apply' / C-c C-t).
;;
;; Two gptel *agents* drive the search:
;;
;;   job-search  -- searches the web for leads and records them in the
;;                  pipeline with the `cv_jobs_add_lead' tool.
;;   job-apply   -- takes the next lead and applies to it in a real browser
;;                  (Playwright MCP), marking or archiving the entry.
;;
;; `cv-jobs-orchestrate' runs both as asynchronous subagents: apply keeps
;; applying while there are leads, and when the pipeline runs dry the search
;; agent is asked for more.  See the "Orchestrator" section at the end.

;;; Code:

(require 'org)
(require 'org-capture)
(require 'org-agenda)
(require 'subr-x)
(require 'seq)
(require 'cl-lib)
(require 'gptel)

(defvar cv-jobs--loaded nil
  "Non-nil once the job-search setup has run in this session.")

(defconst cv-jobs-directory
  (file-name-directory
   (or load-file-name
       (locate-dominating-file default-directory ".dir-locals.el")
       (expand-file-name default-directory)))
  "Directory of the CV / job-search project.")

(defconst cv-jobs-file (expand-file-name "jobs.org" cv-jobs-directory)
  "The Org tracker file for the job search.")

(defconst cv-jobs-cv-file (expand-file-name "cv.typ" cv-jobs-directory)
  "The CV source handed to gptel as context.")

(defcustom cv-jobs-auto-commit nil
  "When non-nil, commit jobs.org automatically after each capture."
  :type 'boolean
  :group 'org)

(defcustom cv-jobs-follow-up-days 7
  "Default number of days until the first follow-up, used by `cv-jobs-apply'."
  :type 'integer
  :group 'org)

(defcustom cv-jobs-bind-org-keys t
  "When non-nil, bind to `org-capture'/`org-agenda'.
Modern Org (9.7 / Emacs 29.2+) no longer installs these global entry points,
so they are unbound unless you bind them.  Set this to nil if you already use
as your own prefix."
  :type 'boolean
  :group 'org)

;; Modern Org does not install the global entry points; provide them.
(when cv-jobs-bind-org-keys
  (dolist (spec '(("C-c c" . org-capture)
                  ("C-c a" . org-agenda)))
    (let ((key (kbd (car spec))))
      (unless (eq (lookup-key global-map key) (cdr spec))
        (define-key global-map key (cdr spec))))))

(defvar cv-jobs--installed-capture-templates nil
  "The exact `org-capture-templates' entries installed by this file.

Remembered so that re-loading this file can replace *its own* templates
without touching any template it did not install -- including the default
ones (or your own) that happen to use the same keys.")

(defun cv-jobs--setup-captures ()
  "Append the job-search capture templates to `org-capture-templates'.

This is strictly additive: every template that already exists -- including
the built-in \"Task\" template, once it is defined as an ordinary entry --
is left untouched and keeps its key.  The job-search entries are only
*appended* after them, so they behave as an optional extra for this project
rather than a replacement for your normal captures.  Re-running refreshes
only the entries installed by this file, so nothing gets duplicated and
nothing foreign is ever removed."
  ;; First drop only the entries this file installed on a previous run.
  (setq org-capture-templates
        (seq-remove (lambda (entry)
                      (member entry cv-jobs--installed-capture-templates))
                    org-capture-templates))
  (setq cv-jobs--installed-capture-templates
        (list '("j" "Job search")
              `("jl" "New lead" entry
                (file+headline ,cv-jobs-file "Pipeline")
                ,(concat
                  "* LEAD %^{Company} — %^{Role}\n"
                  "  :PROPERTIES:\n"
                  "  :URL: %^{URL}\n"
                  "  :LOCATION: %^{Location|Da Nang|Remote|Other}\n"
                  "  :SIZE: %^{Size|small|mid|large}\n"
                  "  :ENGLISH: %^{English-speaking?|yes|unknown|no}\n"
                  "  :CONTACT: %^{Contact}\n"
                  "  :CREATED: %U\n"
                  "  :END:\n"
                  "  %?"))))
  ;; `org-capture-select-template' only falls back to its built-in "Task"
  ;; template when `org-capture-templates' is *empty*.  Since we are about to
  ;; fill it, carry that default over explicitly (unless it is already there,
  ;; e.g. defined in the init) so a plain TODO capture does not disappear.
  (let ((base (or org-capture-templates
                  '( ("t" "Task" entry (file+headline "" "Tasks")
                      "* TODO %?\n  %u\n  %a") ))))
    (setq org-capture-templates
          (append base cv-jobs--installed-capture-templates))))

(defun cv-jobs--setup-agenda ()
  "Wire jobs.org into the agenda and add the job pipeline view."
  (add-to-list 'org-agenda-files cv-jobs-file)
  (unless (assoc "j" org-agenda-custom-commands)
    (add-to-list
     'org-agenda-custom-commands
     '("j" "Job search pipeline"
       ((tags-todo "+TODO=\"LEAD\"" ((org-agenda-overriding-header "Leads")))
        (tags-todo "+TODO=\"APPLIED\"" ((org-agenda-overriding-header "Applied — awaiting reply")))
        (tags-todo "+TODO=\"INTERVIEW\"" ((org-agenda-overriding-header "Interviewing")))
        (todo "OFFER" ((org-agenda-overriding-header "Offers")))
        (agenda "" ((org-agenda-overriding-header "Follow-ups (next 2 weeks)")
                    (org-agenda-span 14)
                    (org-agenda-start-day "-1"))))))))

(defun cv-jobs--setup-buffer ()
  "Configure the jobs.org buffer and its local keys."
  (when (and buffer-file-name (file-equal-p buffer-file-name cv-jobs-file))
    (local-set-key (kbd "C-c j a") #'cv-jobs-agenda)
    (local-set-key (kbd "C-c j c") #'cv-jobs-commit)
    (local-set-key (kbd "C-c j A") #'cv-jobs-apply)
    (local-set-key (kbd "C-c j x") #'cv-jobs-archive)
    (local-set-key (kbd "C-c j X") #'cv-jobs-archive-stale-applied)
    (local-set-key (kbd "C-c j o") #'cv-jobs-orchestrate)
    (local-set-key (kbd "C-c j O") #'cv-jobs-orchestrate-stop)
    ;; Closed companies go to the Archive tree in the same file.
    (setq-local org-archive-location (concat cv-jobs-file "::* Archive"))))

(defun cv-jobs-open ()
  "Open the job-search tracker."
  (interactive)
  (find-file cv-jobs-file))

(defun cv-jobs-reload ()
  "Reload jobsearch.el so edits take effect without restarting Emacs.

`.dir-locals.el' only loads this file when a project buffer is set up, so
a long-running Emacs can be stuck with stale definitions -- including
stale gptel tools.  Run this after editing to refresh everything."
  (interactive)
  (load (expand-file-name "jobsearch.el" cv-jobs-directory) nil nil)
  (message "Reloaded jobsearch.el; gptel tools re-registered."))

(defun cv-jobs-agenda ()
  "Open the job-search agenda pipeline."
  (interactive)
  (org-agenda nil "j"))

(defun cv-jobs--add-note (note)
  "Log NOTE on the entry at point, creating the LOGBOOK drawer if needed.

This replaces the previous `(org-add-note \"...\")' call, which could never
work: `org-add-note' is an interactive command that takes *no* arguments and
prompts for the note, so handing it text raised
`wrong-number-of-arguments: org-add-note 1'.  Inserting the \"Note taken
on ...\" entry directly keeps the call non-interactive and version-warning
free.  The drawer is written at column 0, in the canonical position after the
planning line and the PROPERTIES drawer, which Org parses the same way as the
indented drawers produced by `org-add-note' itself."
  (save-excursion
    (org-back-to-heading t)
    ;; Org marks a note that has a body with a trailing double backslash on
    ;; the "Note taken on ..." line; built with `make-string' so the
    ;; backslashes need no double escaping in this source file.
    (let* ((mark (make-string 2 ?\\))
           (entry (format "Note taken on %s %s\n  %s\n"
                          (format-time-string "%Y-%m-%d %a %H:%M")
                          mark
                          note)))
      (forward-line)
      ;; Step over the planning line and the property drawer, so the LOGBOOK
      ;; drawer lands where Org expects it rather than above them.
      (when (looking-at-p "[ \t]*\\(CLOSED\\|SCHEDULED\\|DEADLINE\\):")
        (forward-line))
      (when (looking-at-p "[ \t]*:PROPERTIES:")
        (when (re-search-forward "^[ \t]*:END:[ \t]*$"
                                 (save-excursion (org-end-of-subtree t t)) t)
          (forward-line)))
      (if (re-search-forward "^[ \t]*:LOGBOOK:[ \t]*$"
                             (save-excursion (org-end-of-subtree t t)) t)
          (progn
            (forward-line)
            (if (re-search-forward "^[ \t]*:END:[ \t]*$"
                                   (save-excursion (org-end-of-subtree t t)) t)
                (progn (beginning-of-line) (insert entry))
              (insert entry)))
        (insert ":LOGBOOK:\n" entry ":END:\n")))))

(defun cv-jobs-apply ()
  "Move the company at point to APPLIED and schedule a follow-up.

This replaces the old duplicate \"application\" entry: the same node just
changes state, so company details are never repeated."
  (interactive)
  (unless (derived-mode-p 'org-mode) (user-error "Not in an Org buffer"))
  (org-back-to-heading t)
  (when (equal (org-get-todo-state) "LEAD")
    (org-todo "APPLIED"))
  ;; Logging the note is bookkeeping: a failure here must not cost us the
  ;; APPLIED transition or the follow-up, so report and carry on.
  (condition-case err
      (cv-jobs--add-note "Application sent.")
    (error (message "Could not log application note: %S" err)))
  (org-schedule nil (format-time-string
                     "%Y-%m-%d"
                     (time-add (current-time)
                               (* cv-jobs-follow-up-days 24 60 60)))))

(defun cv-jobs--record-applied-date ()
  "Stamp the :APPLIED_ON: property whenever an entry enters APPLIED.

Installed on `org-after-todo-state-change-hook' so the date is recorded no
matter how the state changed -- `cv-jobs-apply', `C-c C-t', or a bulk change
from the agenda.  Only entries in `cv-jobs-file' are touched.  The drawer is
created on demand by `org-set-property', so entries captured before this
existed need no migration."
  (when (and (derived-mode-p 'org-mode)
             buffer-file-name
             (file-equal-p buffer-file-name cv-jobs-file)
             (equal (org-get-todo-state) "APPLIED"))
    (save-excursion
      (org-set-property "APPLIED_ON" (format-time-string "%Y-%m-%d")))))

(defun cv-jobs-archive ()
  "Archive the entry at point into the file's Archive tree."
  (interactive)
  (org-back-to-heading t)
  (let ((org-archive-location (concat cv-jobs-file "::* Archive")))
    (org-archive-subtree)))

(defun cv-jobs-archive-stale-applied (&optional days)
  "Archive APPLIED entries whose :APPLIED_ON: is older than DAYS (default 7).

Reuses the same Archive tree as `cv-jobs-archive'.  Candidates are collected
first and archived bottom-up so buffer positions of the remaining ones stay
valid.  With a numeric prefix argument, override the age in days, e.g.
\[universal-argument] 4 M-x cv-jobs-archive-stale-applied for 4 days."
  (interactive "P")
  (let* ((days (if days (prefix-numeric-value days) 7))
         (cutoff (time-subtract (current-time) (days-to-time days)))
         (targets '()))
    (with-current-buffer (find-file-noselect cv-jobs-file)
      (org-map-entries
       (lambda ()
         (when (equal (org-get-todo-state) "APPLIED")
           (let ((stamp (org-entry-get nil "APPLIED_ON")))
             (when (and stamp
                        (time-less-p (org-time-string-to-time stamp) cutoff))
               (push (point-marker) targets)))))
       nil 'file)
      (dolist (m (sort targets (lambda (a b)
                                 (> (marker-position a) (marker-position b)))))
        (goto-char m)
        (let ((org-archive-location (concat cv-jobs-file "::* Archive")))
          (org-archive-subtree))
        (set-marker m nil))
      (message "Archived %d APPLIED entr%s older than %d days"
               (length targets)
               (if (= 1 (length targets)) "y" "ies")
               days))))

(defun cv-jobs-verify ()
  "Report whether the project-local job-search setup is actually active."
  (interactive)
  (let* ((bind (lookup-key global-map (kbd "C-c c")))
         (bind-desc (cond ((keymapp bind) "C-c c is a prefix")
                          ((null bind) "C-c c unbound")
                          (t (format "C-c c => %s" bind)))))
    (message (concat "jobsearch: %s | %s | capture keys: %s | "
                     "jobs.org in agenda: %s")
             (if (featurep 'jobsearch) "loaded" "NOT LOADED")
             bind-desc
             (mapcar #'car (seq-filter #'listp org-capture-templates))
             (if (member cv-jobs-file org-agenda-files) "yes" "no"))))

(defun cv-jobs-setup ()
  "(Re)install the job-search capture, agenda, key and gptel wiring.

Safe to call repeatedly; the capture templates this file adds are refreshed
in place and the hooks and agenda entries are idempotent."
  (interactive)
  (cv-jobs--setup-captures)
  (cv-jobs--setup-agenda)
  (add-hook 'org-mode-hook #'cv-jobs--setup-buffer)
  (add-hook 'org-after-todo-state-change-hook #'cv-jobs--record-applied-date)
  ;; `org-mode-hook' may already have run for the triggering buffer.
  (cv-jobs--setup-buffer)
  (setq cv-jobs--loaded t))

(cv-jobs-setup)

(setq org-modern-todo-faces
      '(("LEAD" . "⟳") ("APPLIED" . "⇒") ("INTERVIEW" . "★")
        ("OFFER" . "★") ("HIRED" . "✔") ("REJECTED" . "✘")))

(setq org-export-with-properties t)

;;; Agents (gptel presets)

;; `job-search' finds leads and records them; `job-apply' applies to them.  The
;; orchestrator at the end of this file runs both as asynchronous subagents.

(gptel-make-preset 'job-search
  :description "Search the web for remote software dev jobs and record leads."
  :backend "Deepseek"
  :model 'deepseek-v4-flash
  :system "You find remote software development jobs given a CV and previous application statuses as context.
Focus less on job boards/agencies and more on finding individual software development companies that are hiring.
You are immune to job market propaganda and know exactly how to find the perfect remote job for the provided CV.
Record every promising new lead you find with the cv_jobs_add_lead tool so it lands in the pipeline; skip companies that are already tracked."
  :context (list cv-jobs-file cv-jobs-cv-file)
  :tools '("mcp-websearch" "mcp-fetch" "cv_jobs_add_lead"))

;;; Applying to leads (Playwright MCP)

;; The `job-apply' preset turns the browser into the applicant: it fetches the
;; next LEAD with the `cv_jobs_next_lead' tool, then drives a real browser
;; through the Playwright MCP server to fill in, and submit, the application.
;; The `playwright-mcp' binary comes from the project dev shell (see
;; flake.nix), so Emacs has to inherit the dev-shell environment (e.g. be
;; started from `~/src/cv' under direnv) for it to be on PATH.

(require 'mcp)
(require 'gptel-integrations)

(defvar cv-jobs--next-lead-marker nil
  "Marker to the lead last returned by `cv_jobs_next_lead'.

Lets `cv_jobs_mark_applied' find the entry to update without the agent
having to pass a buffer position around.")

(defconst cv-jobs-lead-properties '("URL" "LOCATION" "SIZE" "ENGLISH" "CONTACT")
  "Entry properties reported for a lead by `cv_jobs_next_lead'.")

(defun cv-jobs--describe-lead ()
  "Return a description of the lead at point, for the LLM."
  (concat
   "Next lead in the pipeline:\n"
   "Heading: " (org-get-heading t t t t) "\n"
   (mapconcat (lambda (prop)
                (format "%s: %s" prop (or (org-entry-get nil prop) "-")))
              cv-jobs-lead-properties "\n")))

(defun cv-jobs-next-lead ()
  "Return the next LEAD in the pipeline and remember where it is.

Leads are searched in file order under the \"Pipeline\" tree of
`cv-jobs-file'; archived entries are ignored.  Returns an explanatory
string instead when the pipeline has run dry.  Intended as a gptel tool
for the `job-apply' preset."
  (with-current-buffer (find-file-noselect cv-jobs-file)
    (save-excursion
      (save-restriction
        (widen)
        (goto-char (point-min))
        (setq cv-jobs--next-lead-marker
              (when (re-search-forward "^\\*+ +Pipeline\\b" nil t)
                (org-back-to-heading t)
                (catch 'found
                  (org-map-entries
                   (lambda ()
                     (when (equal (org-get-todo-state) "LEAD")
                       (throw 'found (point-marker))))
                   "+TODO=\"LEAD\""
                   'tree))))
        (if cv-jobs--next-lead-marker
            (save-excursion
              (goto-char cv-jobs--next-lead-marker)
              (org-back-to-heading t)
              (cv-jobs--describe-lead))
          "No LEAD entries left in the pipeline.")))))

(defun cv-jobs-mark-applied ()
  "Mark the lead from the last `cv-jobs-next-lead' call as APPLIED.

Reuses `cv-jobs-apply', so the follow-up is scheduled and the
:APPLIED_ON: property stamped exactly as for a manual application.
Intended as a gptel tool to call once the browser has submitted the
application."
  (if (not (and cv-jobs--next-lead-marker
                (marker-buffer cv-jobs--next-lead-marker)))
      "No lead is selected; call cv_jobs_next_lead first."
    (with-current-buffer (marker-buffer cv-jobs--next-lead-marker)
      (save-excursion
        (goto-char cv-jobs--next-lead-marker)
        (org-back-to-heading t)
        (let ((heading (org-get-heading t t t t)))
          (cv-jobs-apply)
          (setq cv-jobs--next-lead-marker nil)
          (format "Marked %s as APPLIED and scheduled a follow-up." heading))))))

(gptel-make-tool
 :name "cv_jobs_next_lead"
 :function #'cv-jobs-next-lead
 :description "Return the next LEAD from the job-search pipeline in jobs.org, with its URL, location, size, English requirement and contact. Call this first to choose which company to apply to."
 :args nil
 :category "jobsearch")

(defun cv-jobs-archive-lead (&optional reason)
  "Archive the lead selected by the last `cv-jobs-next-lead' call.

Reuses `cv-jobs-archive' (the command bound to C-c j x), so the tool cannot
drift from the proven archive path.  The lead keeps its current TODO state.

REASON, when it is a non-blank string, is logged as a note on the entry;
otherwise a default note is used.  Logging the note is best-effort and
never blocks the archive."
  (if (not (and cv-jobs--next-lead-marker
                (marker-buffer cv-jobs--next-lead-marker)))
      "No lead is selected; call cv_jobs_next_lead first."
    (with-current-buffer (marker-buffer cv-jobs--next-lead-marker)
      (goto-char cv-jobs--next-lead-marker)
      (org-back-to-heading t)
      (let* ((heading (org-get-heading t t t t))
             (note (if (and (stringp reason) (not (string-blank-p reason)))
                       reason
                     "Archiving due to application failure")))
        (condition-case err
            (progn
              (ignore-errors (cv-jobs--add-note note))
              (cv-jobs-archive)
              (setq cv-jobs--next-lead-marker nil)
              (format "Archived %s without applying: %s" heading note))
          (error
           (format "Failed to archive %s: %S" heading err)))))))

(defun cv-jobs--lead-url-p (url)
  "Return non-nil if a lead with URL is already tracked in jobs.org."
  (with-current-buffer (find-file-noselect cv-jobs-file)
    (org-with-wide-buffer
     (goto-char (point-min))
     (re-search-forward
      (concat "^[ \t]*:URL:[ \t]*" (regexp-quote url) "[ \t]*$") nil t))))

(defun cv-jobs--pipeline-insertion-point ()
  "Return the position where a new entry under \"Pipeline\" belongs.

This is the buffer position of the next top-level heading after the
\"Pipeline\" tree (e.g. \"* Archive\"), or the end of the buffer when the
\"Pipeline\" tree is the last one."
  (save-excursion
    (goto-char (point-min))
    (if (re-search-forward "^\\*+ +Pipeline\\b" nil t)
        (let ((level (or (org-current-level) 1)))
          (forward-line 1)
          (if (re-search-forward (format "^\\*\\{1,%d\\} " level) nil t)
              (match-beginning 0)
            (point-max)))
      (point-max))))

(defun cv-jobs--format-properties (url location size english contact notes)
  "Return the Org property drawer body for the given lead values.

Values that are nil or blank are omitted."
  (cl-loop for pair in (list (cons "URL" url) (cons "LOCATION" location)
                             (cons "SIZE" size) (cons "ENGLISH" english)
                             (cons "CONTACT" contact) (cons "NOTES" notes))
           for key = (car pair)
           for value = (cdr pair)
           when (and value (not (string-blank-p (string-trim value))))
           concat (format "   :%s: %s\n" key (string-trim value))))

(defun cv-jobs--insert-lead-entry (company role url location size english contact notes)
  "Insert a new LEAD entry for COMPANY/ROLE into jobs.org and save the file."
  (with-current-buffer (find-file-noselect cv-jobs-file)
    (org-with-wide-buffer
     (let* ((end (cv-jobs--pipeline-insertion-point))
            (props (cv-jobs--format-properties url location size english contact notes))
            (entry (concat
                    (format "** LEAD %s — %s\n" company role)
                    "   :PROPERTIES:\n"
                    props
                    (format "   :CREATED: %s\n" (format-time-string "%Y-%m-%d"))
                    "   :END:\n")))
       (goto-char end)
       (skip-chars-backward " \t\n")
       (let ((ins (point)))
         (delete-region ins end)
         (goto-char ins)
         (insert "\n\n" entry))
       (save-buffer)))))

(defun cv-jobs-add-lead (company role &optional url location size english contact notes)
  "Add a new LEAD for COMPANY/ROLE to the job-search pipeline.

URL, LOCATION, SIZE, ENGLISH, CONTACT and NOTES are optional strings.  If a
lead with the same URL is already tracked nothing is added, so the tool can
be called repeatedly without creating duplicates.  Returns a short status
string.  Intended as a gptel tool for the `job-search' agent."
  (let ((company (string-trim (or company "")))
        (role (string-trim (or role ""))))
    (cond
     ((string-blank-p company)
      "Error: company is required.")
     ((and url (not (string-blank-p url)) (cv-jobs--lead-url-p url))
      (format "Skipped %s: a lead with that URL already exists." company))
     (t
      (cv-jobs--insert-lead-entry company role url location size english contact notes)
      (format "Added %s — %s" company role)))))

(gptel-make-tool
 :name "cv_jobs_mark_applied"
 :function #'cv-jobs-mark-applied
 :description "Move the lead most recently returned by cv_jobs_next_lead from LEAD to APPLIED and schedule the follow-up. Call this once the application has been submitted."
 :args nil
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_archive_lead"
 :function #'cv-jobs-archive-lead
 :description "Archive the lead most recently returned by cv_jobs_next_lead after abandoning it, recording the reason as a note on the entry. Use this when the application cannot be completed -- for example a required field whose answer is not in the CV and must not be fabricated, or a broken/dead application form. Call this instead of marking the lead APPLIED."
 :args '((:name "reason"
          :type string
          :optional t
          :description "Why the application could not be completed and the lead is being archived."))
 :category "jobsearch")

(gptel-make-tool
 :name "cv_jobs_add_lead"
 :function #'cv-jobs-add-lead
 :description "Add a new LEAD entry to the job-search pipeline in jobs.org. Use this to record each promising new company/role you find. Provide the company and role; url, location, size, english, contact and notes are optional. Leads whose URL is already in the file are skipped, so it is safe to call repeatedly."
 :args '((:name "company" :type string :description "Company name.")
         (:name "role" :type string :description "Role or job title.")
         (:name "url" :type string :optional t :description "URL of the job posting or careers page.")
         (:name "location" :type string :optional t :description "Where the role is based, e.g. Remote.")
         (:name "size" :type string :enum ["small" "mid" "large"] :optional t :description "Company size.")
         (:name "english" :type string :enum ["yes" "unknown" "no"] :optional t :description "Whether the role uses English.")
         (:name "contact" :type string :optional t :description "Contact email, if known.")
         (:name "notes" :type string :optional t :description "Short notes about the role."))
 :category "jobsearch")

(gptel-make-preset 'job-apply
  :description "Apply to the next LEAD in the pipeline in a real browser."
  :parents 'job-search
  :pre (lambda ()
         (add-to-list 'mcp-hub-servers
                      '("playwright" . (
                                        :command "playwright-mcp"
                                        :args ("--executable-path" "~/src/cv/.devenv/profile/bin/chromium-sandboxed"))))
         (gptel-mcp-connect '("playwright") t))
  :system "You apply for software jobs on behalf of the user by driving a web
browser. First call the cv_jobs_next_lead tool to get the next lead and its URL,
then use the playwright mcp to navigate to the url and fill out an application form.
If the url list more than one job find the most suitable opening and apply to that one.
Do not apply to more than one job per lead. The relevant files are
~/src/cv/result/Ivan_Dimitrov_Resume_Software_Developer.pdf
~/src/cv/result/Ivan_Dimitrov_Resume_Software_Developer_ATS.pdf
~/src/cv/cv.typ
~/src/cv/cv-ats.typ
Other relevant information:
Name: Ivan Dimitrov
Email: ivan@idimitrov.dev
Phone number +84898532337
Upwork url: https://www.upwork.com/freelancers/idimitrov
LinkedIn url: https://www.linkedin.com/in/devidimitrov
GitHub url: https://github.com/ivandimitrov8080
GitLab url: https://gitlab.com/ivandimitrov8080
Resume url: https://idimitrov.dev
Right to work in EU and B2B remote.
Business is registered in Bulgaria.
VERY IMPORTANT:
Do not fabricate information. Inferring information from the CV is allowed. Do not fill out form fields that are not absolutely required. Avoid free text fields that want an open answer. If the field is required but the information is not present in the CV then stop applying. If you stop applying, or otherwise cannot complete the application, do not leave the lead in the pipeline: call the cv_jobs_archive_lead tool with a short reason explaining why, so the lead is archived with a note instead."
  :tools '("mcp-playwright" "cv_jobs_next_lead" "cv_jobs_mark_applied"
           "cv_jobs_archive_lead"))

;;; Orchestrator: run the search and apply agents as asynchronous subagents

;; The orchestrator drives the two presets above as *subagents*.  It launches
;; one agent at a time with `gptel-request' and resumes from the request's
;; terminal state (DONE / ERRS / ABRT), so Emacs is never blocked: the agents
;; run through gptel's asynchronous request machinery.
;;
;;   job-apply   -- keeps applying while there are still leads in the pipeline,
;;                  until `cv_jobs_next_lead' reports it is dry.
;;   job-search  -- searches for jobs and records new leads, refilling the
;;                  pipeline.
;;
;; The loop is: apply until the pipeline is dry, then search; if the search
;; adds leads, apply again; stop once a search round adds nothing new (or a
;; safety cap is hit).  Start it with `cv-jobs-orchestrate' (C-c j o in
;; jobs.org) and stop it with `cv-jobs-orchestrate-stop' (C-c j O).

(defcustom cv-jobs-orchestrator-max-search-rounds 1
  "Fruitless search rounds to tolerate before the orchestrator stops.

The orchestrator stops once this many consecutive search rounds have added
no new leads to the pipeline.  The default, 1, stops as soon as a search
round finds nothing new."
  :type 'integer
  :group 'org)

(defcustom cv-jobs-orchestrator-max-apply-rounds 25
  "Safety cap on the number of consecutive apply rounds.

Each round drains the pipeline with the apply agent.  If the pipeline is
still not empty after this many rounds the orchestrator stops, to avoid
repeating applications forever."
  :type 'integer
  :group 'org)

(defcustom cv-jobs-orchestrator-max-errors 3
  "Consecutive failed agent runs to tolerate before the orchestrator stops."
  :type 'integer
  :group 'org)

(defconst cv-jobs-search-prompt
  (concat "Find new remote software-development job leads that fit the CV and "
          "add them to the pipeline. Focus on individual companies that are "
          "hiring rather than job boards. For every promising lead call the "
          "cv_jobs_add_lead tool to record it; skip companies that are "
          "already tracked. Stop once you have added the leads you found.")
  "Prompt used to launch the `job-search' agent.")

(defconst cv-jobs-apply-prompt
  (concat "Apply to the leads in the pipeline. Repeatedly call "
          "cv_jobs_next_lead to get the next lead, apply to it in the browser, "
          "then call cv_jobs_mark_applied (on success) or cv_jobs_archive_lead "
          "(if it cannot be completed). Keep going until cv_jobs_next_lead "
          "reports that no leads are left.")
  "Prompt used to launch the `job-apply' agent.")

(defvar cv-jobs-orchestrator--running nil
  "Non-nil while the job-search orchestrator is active.")

(defvar cv-jobs-orchestrator--buffer nil
  "Work buffer of the agent the orchestrator is currently running.")

(defvar cv-jobs-orchestrator--phase nil
  "Preset name of the agent the orchestrator is currently running.")

(defvar cv-jobs-orchestrator--fruitless-searches 0
  "Number of consecutive search rounds that added no leads.")

(defvar cv-jobs-orchestrator--apply-rounds 0
  "Number of consecutive apply rounds run so far.")

(defvar cv-jobs-orchestrator--errors 0
  "Number of consecutive failed agent runs.")

(defvar cv-jobs-orchestrator--search-before 0
  "Lead count recorded just before the current search round started.")

(defvar cv-jobs--transforms
  (progn
    (require 'gptel-context nil t)
    (and (fboundp 'gptel-context--wrap)
         '(gptel--transform-add-context)))
  "Prompt transforms used when running the agents.

Includes gptel's context transform so the preset's `:context' files (the CV
and jobs.org) are sent with each request.  Nil when gptel's context support
is unavailable.")

(defun cv-jobs-count-leads ()
  "Return the number of LEAD entries in the pipeline.

Only entries under the \"Pipeline\" tree are counted, so archived leads
(which keep their LEAD state) are not counted."
  (let ((n 0))
    (with-current-buffer (find-file-noselect cv-jobs-file)
      (org-with-wide-buffer
       (goto-char (point-min))
       (when (re-search-forward "^\\*+ +Pipeline\\b" nil t)
         (org-back-to-heading t)
         (org-map-entries (lambda () (cl-incf n)) "+TODO=\"LEAD\"" 'tree))))
    n))

(defun cv-jobs--agent-buffer (preset)
  "Return the persistent work buffer used to run the PRESET agent."
  (get-buffer-create (format "*cv-jobs:%s*" preset)))

(defun cv-jobs--configure-agent-buffer (preset)
  "Set up the current buffer to run the PRESET agent.

The gptel preset is applied buffer-locally, so the agent's backend, model,
system message, tools and context are used without touching any other
buffer.  Tool-call confirmation is disabled so the agent never blocks on a
prompt, and tool results are not inserted, so the buffer stays small."
  (unless (eq major-mode 'fundamental-mode)
    (fundamental-mode))
  ;; Ensure `gptel-tools' has a buffer-local binding before the preset is
  ;; applied: the `:pre' hook of `job-apply' registers MCP tools with
  ;; `cl-pushnew', and without a local binding that would leak them into the
  ;; user's global `gptel-tools'.
  (set (make-local-variable 'gptel-tools) nil)
  (setq-local gptel-confirm-tool-calls nil)
  (setq-local gptel-include-tool-results nil)
  (gptel--apply-preset
   preset
   (lambda (sym val) (set (make-local-variable sym) val))))

(defun cv-jobs--agent-fsm-handlers (after)
  "Return request FSM handlers that call AFTER with the terminal state.

The result is a copy of `gptel-request--handlers' with AFTER appended to the
handler list of the terminal states DONE, ERRS and ABRT, so AFTER runs once
when the request finishes (successfully or not)."
  (let* ((handlers (copy-tree gptel-request--handlers))
         (make-handler (lambda (state) (lambda (_fsm) (funcall after state)))))
    (dolist (state '(DONE ERRS ABRT))
      (let ((cell (assq state handlers)))
        (when cell
          (setcdr cell (append (cdr cell) (list (funcall make-handler state)))))))
    handlers))

(defun cv-jobs--run-agent (preset prompt after)
  "Run the PRESET agent with PROMPT asynchronously; call AFTER when done.

AFTER is called with the request's terminal state symbol (`DONE', `ERRS' or
`ABRT').  Returns the agent's work buffer, or nil if the agent could not be
started."
  (let ((buf (cv-jobs--agent-buffer preset)))
    (with-current-buffer buf
      (condition-case err
          (cv-jobs--configure-agent-buffer preset)
        (error
         (message "cv-jobs: could not start %s agent: %S" preset err)
         (setq buf nil))))
    (when (and buf (buffer-live-p buf))
      (with-current-buffer buf
        (goto-char (point-max))
        (insert (format "\n\n===== %s [%s] =====\n%s\n\n"
                        (format-time-string "%F %T") preset prompt))
        (gptel-request prompt
                       :stream nil
                       :transforms cv-jobs--transforms
                       :fsm (gptel-make-fsm
                             :table gptel-request--transitions
                             :handlers (cv-jobs--agent-fsm-handlers after))))
      (setq cv-jobs-orchestrator--buffer buf
            cv-jobs-orchestrator--phase preset))
    buf))

(defun cv-jobs-orchestrator--finish (reason)
  "Stop the orchestrator, reporting REASON."
  (setq cv-jobs-orchestrator--running nil
        cv-jobs-orchestrator--phase nil)
  (message "Job-search orchestrator stopped: %s" reason))

(defun cv-jobs-orchestrator--after-error (phase state)
  "Handle a failed PHASE agent run that ended in STATE.

Retries the step a few times before giving up, so a transient network glitch
does not end an otherwise unattended run."
  (cl-incf cv-jobs-orchestrator--errors)
  (if (> cv-jobs-orchestrator--errors cv-jobs-orchestrator-max-errors)
      (cv-jobs-orchestrator--finish
       (format "%s agent ended in %s %d time(s)"
               phase state cv-jobs-orchestrator--errors))
    (progn
      (message "cv-jobs: %s agent ended in %s; retrying (%d/%d)"
               phase state cv-jobs-orchestrator--errors
               cv-jobs-orchestrator-max-errors)
      (run-at-time 5 nil #'cv-jobs-orchestrator--step))))

(defun cv-jobs-orchestrator--after-apply (state)
  "Handle the end of an apply round that reached STATE."
  (when cv-jobs-orchestrator--running
    (if (eq state 'DONE)
        (progn
          (setq cv-jobs-orchestrator--errors 0)
          (message "cv-jobs: apply round finished, %d lead(s) left"
                   (cv-jobs-count-leads))
          (run-at-time 0.5 nil #'cv-jobs-orchestrator--step))
      (cv-jobs-orchestrator--after-error 'apply state))))

(defun cv-jobs-orchestrator--after-search (state)
  "Handle the end of a search round that reached STATE."
  (when cv-jobs-orchestrator--running
    (if (eq state 'DONE)
        (cv-jobs--orchestrator-search-result)
      (cv-jobs-orchestrator--after-error 'search state))))

(defun cv-jobs--orchestrator-search-result ()
  "Handle the result of a completed search round.

Records whether the round added any leads and decides whether to keep going."
  (let* ((added (- (cv-jobs-count-leads) cv-jobs-orchestrator--search-before)))
    (setq cv-jobs-orchestrator--errors 0)
    (if (> added 0)
        (setq cv-jobs-orchestrator--fruitless-searches 0
              cv-jobs-orchestrator--apply-rounds 0)
      (cl-incf cv-jobs-orchestrator--fruitless-searches))
    (cond
     ((> added 0)
      (message "cv-jobs: search added %d lead(s)" added)
      (run-at-time 0.5 nil #'cv-jobs-orchestrator--step))
     ((>= cv-jobs-orchestrator--fruitless-searches
          cv-jobs-orchestrator-max-search-rounds)
      (cv-jobs-orchestrator--finish "search found no new leads"))
     (t
      (message "cv-jobs: search found nothing new; searching again")
      (run-at-time 0.5 nil #'cv-jobs-orchestrator--step)))))

(defun cv-jobs-orchestrator--step ()
  "Decide and launch the next orchestrator step."
  (cond
   ((not cv-jobs-orchestrator--running) nil)
   ((zerop (cv-jobs-count-leads)) (cv-jobs-orchestrator--start-search))
   (t (cv-jobs-orchestrator--start-apply))))

(defun cv-jobs-orchestrator--start-apply ()
  "Launch an apply round, unless the round cap has been reached."
  (cond
   ((>= cv-jobs-orchestrator--apply-rounds cv-jobs-orchestrator-max-apply-rounds)
    (cv-jobs-orchestrator--finish
     (format "apply-round limit (%d) reached with %d lead(s) left"
             cv-jobs-orchestrator-max-apply-rounds (cv-jobs-count-leads))))
   (t
    (cl-incf cv-jobs-orchestrator--apply-rounds)
    (message "cv-jobs: applying (%d lead(s) in the pipeline)"
             (cv-jobs-count-leads))
    (unless (cv-jobs--run-agent 'job-apply cv-jobs-apply-prompt
                                #'cv-jobs-orchestrator--after-apply)
      (cv-jobs-orchestrator--after-error 'apply 'ERRS)))))

(defun cv-jobs-orchestrator--start-search ()
  "Launch a search round, unless the fruitless-search cap has been reached."
  (cond
   ((>= cv-jobs-orchestrator--fruitless-searches
        cv-jobs-orchestrator-max-search-rounds)
    (cv-jobs-orchestrator--finish "no leads left and search is exhausted"))
   (t
    (setq cv-jobs-orchestrator--search-before (cv-jobs-count-leads))
    (message "cv-jobs: searching for new leads")
    (unless (cv-jobs--run-agent 'job-search cv-jobs-search-prompt
                                #'cv-jobs-orchestrator--after-search)
      (cv-jobs-orchestrator--after-error 'search 'ERRS)))))

(defun cv-jobs--abort-agent-buffer (buf)
  "Abort any gptel request running in BUF."
  (when (buffer-live-p buf)
    (with-current-buffer buf
      (ignore-errors (gptel-abort (current-buffer))))))

;;;###autoload
(defun cv-jobs-orchestrate ()
  "Drive the `job-search' and `job-apply' agents as asynchronous subagents.

The apply agent keeps applying to leads while the pipeline still has any;
once it runs dry the search agent is asked for more leads, and the cycle
repeats until a search round adds nothing new (or a safety cap is hit).
Emacs stays responsive throughout: each agent runs through gptel's
asynchronous request machinery and the orchestrator resumes from the
request's completion handler.

Stop the run early with `cv-jobs-orchestrate-stop'."
  (interactive)
  (when cv-jobs-orchestrator--running
    (user-error "Orchestrator already running; use `cv-jobs-orchestrate-stop'"))
  (setq cv-jobs-orchestrator--running t
        cv-jobs-orchestrator--fruitless-searches 0
        cv-jobs-orchestrator--apply-rounds 0
        cv-jobs-orchestrator--errors 0
        cv-jobs-orchestrator--search-before 0)
  (message "Job-search orchestrator started (%d lead(s) in the pipeline)"
           (cv-jobs-count-leads))
  (cv-jobs-orchestrator--step))

;;;###autoload
(defun cv-jobs-orchestrate-stop ()
  "Stop the job-search orchestrator and abort the current agent request."
  (interactive)
  (cond
   ((not cv-jobs-orchestrator--running)
    (message "Job-search orchestrator is not running"))
   (t
    (setq cv-jobs-orchestrator--running nil)
    (cv-jobs--abort-agent-buffer cv-jobs-orchestrator--buffer)
    (message "Job-search orchestrator stopped"))))

(provide 'jobsearch)
;;; jobsearch.el ends here
