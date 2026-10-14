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
;; Nothing here modifies the global `jobsearch' preset.

;;; Code:

(require 'org)
(require 'org-capture)
(require 'org-agenda)
(require 'subr-x)
(require 'seq)
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
    ;; Closed companies go to the Archive tree in the same file.
    (setq-local org-archive-location (concat cv-jobs-file "::* Archive"))))

(defun cv-jobs-open ()
  "Open the job-search tracker."
  (interactive)
  (find-file cv-jobs-file))

(defun cv-jobs-agenda ()
  "Open the job-search agenda pipeline."
  (interactive)
  (org-agenda nil "j"))

(defun cv-jobs-apply ()
  "Move the company at point to APPLIED and schedule a follow-up.

This replaces the old duplicate \"application\" entry: the same node just
changes state, so company details are never repeated."
  (interactive)
  (unless (derived-mode-p 'org-mode) (user-error "Not in an Org buffer"))
  (org-back-to-heading t)
  (when (equal (org-get-todo-state) "LEAD")
    (org-todo "APPLIED"))
  (org-add-note "Application sent.")
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

(gptel-make-preset 'jobsearch
  :description "Search the web for remote software dev jobs."
  :backend "Deepseek"
  :model 'deepseek-v4-flash
  :system "You find remote software development jobs given a CV and previous application statuses as context.
Focus less on job boards/agencies and more on finding individual software development companies that are hiring.
You are immune to job market propaganda and know exactly how to find the perfect remote job for the provided CV."
  :context '("~/src/cv/jobs.org" "~/src/cv/cv.typ")
  :tools '("mcp-websearch" "mcp-fetch"))

;;; Applying to leads (Playwright MCP)

;; The `jobsearch-apply' preset turns the browser into the applicant: it fetches
;; the next LEAD with the `cv_jobs_next_lead' tool, then drives a real browser
;; through the Playwright MCP server to fill in, and submit, the application.
;; The `playwright-mcp' binary comes from the project dev shell (see
;; flake.nix), so Emacs has to inherit the dev-shell environment (e.g. be
;; started from `~/src/cv' under direnv) for it to be on PATH.

(defconst cv-jobs-playwright-server "playwright"
  "Name of the Playwright MCP server used by `jobsearch-apply'.")

(require 'mcp)
(require 'gptel-integrations)

;; Register the server with the MCP hub but do not start it here; the preset's
;; :pre hook starts it on demand.
(unless (assoc cv-jobs-playwright-server mcp-hub-servers)
  (add-to-list 'mcp-hub-servers
               (cons cv-jobs-playwright-server '(:command "playwright-mcp"))))

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
for the `jobsearch-apply' preset."
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

(gptel-make-tool
 :name "cv_jobs_mark_applied"
 :function #'cv-jobs-mark-applied
 :description "Move the lead most recently returned by cv_jobs_next_lead from LEAD to APPLIED and schedule the follow-up. Call this once the application has been submitted."
 :args nil
 :category "jobsearch")

(gptel-make-preset 'jobsearch-apply
  :description "Apply to the next LEAD in the pipeline in a real browser."
  :parents 'jobsearch
  :pre (lambda ()
         (gptel-mcp-connect (list cv-jobs-playwright-server) 'sync))
  :system "You apply for software jobs on behalf of the user by driving a web
browser. First call the cv_jobs_next_lead tool to get the next lead and its URL,
then use the playwright mcp to navigate to the url and fill out an application form.
If the url list more than one job find the most suitable opening and apply to that one.
Do not apply to more than one job per lead. The relevant files are
~/src/cv/result/Ivan_Dimitrov_Resume_Software_Developer.pdf
~/src/cv/result/Ivan_Dimitrov_Resume_Software_Developer_ATS.pdf
~/src/cv/cv.typ
~/src/cv/cv-ats.typ
."
  :tools '("mcp-playwright" "cv_jobs_next_lead" "cv_jobs_mark_applied"))

(provide 'jobsearch)
;;; jobsearch.el ends here
