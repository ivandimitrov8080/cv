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
  "When non-nil, bind \"C-c c\" and \"C-c a\" to `org-capture'/`org-agenda'.

Modern Org (9.7 / Emacs 29.2+) no longer installs these global entry points,
so they are unbound unless you bind them.  Set this to nil if you already use
\"C-c c\" as your own prefix."
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
    (local-set-key (kbd "C-c j g") #'cv-jobsearch)
    (local-set-key (kbd "C-c j A") #'cv-jobs-apply)
    (local-set-key (kbd "C-c j x") #'cv-jobs-archive)
    ;; Closed companies go to the Archive tree in the same file.
    (setq-local org-archive-location (concat cv-jobs-file "::* Archive"))))

(defun cv-jobs--setup-gptel ()
  "Derive the `jobsearch-cv' gptel preset from the global `jobsearch' one."
  (when (gptel-get-preset 'jobsearch)
    (gptel-make-preset 'jobsearch-cv
      :description "jobsearch + local Org tracker (jobs.org)"
      :parents '(jobsearch)
      :context `(:append (,cv-jobs-file))
      :tools '(:append ("mcp-filesystem" "mcp-git"))
      :system
      `(:function
        ,(lambda (orig)
           (concat orig "\n\n"
                   "You also maintain the user's lead tracker at " cv-jobs-file
                   ". The user is looking for small, English-speaking software "
                   "firms in or near Da Nang, Vietnam. Consult jobs.org first "
                   "and never duplicate an existing company. There is ONE entry "
                   "per company, filed under the 'Pipeline' heading, and its "
                   "TODO state tracks the stage. When you identify a strong "
                   "lead, append (never rewrite) a new entry under 'Pipeline':\n"
                   "* LEAD <Company> — <Role>\n"
                   "  :PROPERTIES:\n"
                   "  :URL: <url>\n"
                   "  :LOCATION: <city>\n"
                   "  :SIZE: <small|mid|large>\n"
                   "  :ENGLISH: <yes|unknown|no>\n"
                   "  :CREATED: <YYYY-MM-DD>\n"
                   "  :END:\n"))))))

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
  (when (eq (org-get-todo-state) 'LEAD)
    (org-todo "APPLIED"))
  (org-add-note "Application sent.")
  (org-schedule nil (format-time-string
                     "%Y-%m-%d"
                     (time-add (current-time)
                               (* cv-jobs-follow-up-days 24 60 60)))))

(defun cv-jobs-archive ()
  "Archive the entry at point into the file's Archive tree."
  (interactive)
  (org-back-to-heading t)
  (let ((org-archive-location (concat cv-jobs-file "::* Archive")))
    (org-archive-subtree)))

(defun cv-jobs-commit (&optional message)
  "Stage and commit jobs.org to the cv git repository."
  (interactive)
  (let* ((default-directory (file-name-as-directory cv-jobs-directory))
         (msg (or message "jobsearch: update tracker"))
         (status (string-trim
                  (shell-command-to-string "git status --porcelain jobs.org"))))
    (if (string-empty-p status)
        (message "Nothing to commit in jobs.org")
      (call-process "git" nil nil nil "add" "jobs.org")
      (call-process "git" nil nil nil "commit" "-m" msg "jobs.org")
      (message "Committed jobs.org: %s" msg))))

(defun cv-jobsearch ()
  "Open a gptel buffer primed with the `jobsearch-cv' preset."
  (interactive)
  (require 'gptel)
  (let ((default-directory (file-name-as-directory cv-jobs-directory)))
    (gptel "*cv-jobsearch*")
    (goto-char (point-max))
    (unless (save-excursion
              (beginning-of-line)
              (looking-at-p ".*@jobsearch-cv "))
      (insert "@jobsearch-cv "))
    (message "jobsearch-cv ready — type a request and send with C-c C-c")))

(defun cv-jobs--maybe-auto-commit (&rest _)
  "Commit jobs.org after a capture, when `cv-jobs-auto-commit' is non-nil."
  (when cv-jobs-auto-commit (cv-jobs-commit)))

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
  (add-hook 'org-capture-after-finalize-hook #'cv-jobs--maybe-auto-commit)
  (with-eval-after-load 'gptel (cv-jobs--setup-gptel))
  ;; `org-mode-hook' may already have run for the triggering buffer.
  (cv-jobs--setup-buffer)
  (setq cv-jobs--loaded t))

(cv-jobs-setup)

(gptel-make-preset 'jobsearch
  :description "Search the web for remote software dev jobs."
  :backend "Deepseek"
  :model 'deepseek-v4-flash
  :system "You find remote software development jobs given a CV as context.
Focus less on job boards/agencies and more on finding individual software development companies that are hiring.
You are immune to job market propaganda and know exactly how to find the perfect remote job for the provided CV."
  :context '("~/src/cv/cv.typ")
  :tools '("mcp-websearch" "mcp-fetch"))

(provide 'jobsearch)
;;; jobsearch.el ends here
