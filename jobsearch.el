;;; jobsearch.el --- Project-local job-search workflow  -*- lexical-binding: t; -*-

;;; Commentary:

;; This file is loaded on demand from ~/src/cv/.dir-locals.el, so none of the
;; job-search / gptel glue has to live in the main init file.
;;
;; It provides, additively and idempotently:
;;
;;   * two Org capture templates (a company "lead" and an "application")
;;   * jobs.org wired into `org-agenda' plus a dedicated `C-c a j' pipeline
;;   * local keys under C-c j inside jobs.org
;;   * a `jobsearch-cv' gptel preset derived from the global `jobsearch'
;;     preset, adding jobs.org as context and the MCP filesystem/git tools
;;   * helpers to open the tracker, view the pipeline and commit with git
;;
;; Nothing here modifies the global `jobsearch' preset.

;;; Code:

(require 'org)
(require 'org-capture)  ; installs the global "C-c c" binding
(require 'subr-x)
(require 'seq)

(defvar cv-jobs--loaded nil
  "Non-nil once the CV job-search setup has run in this session.")

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

(defun cv-jobs--register-capture (entry)
  "Register org-capture ENTRY unless its key already exists."
  (unless (assoc (car entry) org-capture-templates)
    (setq org-capture-templates
          (append org-capture-templates (list entry)))))

(defun cv-jobs--setup-captures ()
  "Install the job-search Org capture templates."
  (cv-jobs--register-capture '("j" "Job search"))
  (cv-jobs--register-capture
   `("jl" "Lead (company found)" entry
     (file+headline ,cv-jobs-file "Leads")
     ,(concat
       "* LEAD %^{Company} — %^{Role}\n"
       "  :PROPERTIES:\n"
       "  :URL: %^{URL}\n"
       "  :LOCATION: %^{Location|Da Nang|Remote|Other}\n"
       "  :SIZE: %^{Size|Small|Mid|Large}\n"
       "  :ENGLISH: %^{English-speaking?|yes|unknown|no}\n"
       "  :CONTACT: %^{Contact}\n"
       "  :CREATED: %U\n"
       "  :END:\n"
       "  %?")))
  (cv-jobs--register-capture
   `("ja" "Application (with follow-up)" entry
     (file+headline ,cv-jobs-file "Applications")
     ,(concat
       "* APPLIED %^{Company} — %^{Role}\n"
       "  :PROPERTIES:\n"
       "  :URL: %^{URL}\n"
       "  :APPLIED: %U\n"
       "  :END:\n"
       "  SCHEDULED: %^{First follow-up}t\n"
       "  %?"))))

(defun cv-jobs--setup-agenda ()
  "Wire jobs.org into the agenda and add the job pipeline view."
  (add-to-list 'org-agenda-files cv-jobs-file)
  (unless (assoc "j" org-agenda-custom-commands)
    (add-to-list
     'org-agenda-custom-commands
     '("j" "Job search pipeline"
       ((tags-todo "+TODO=\"LEAD\"" ((org-agenda-overriding-header "Open leads")))
        (tags-todo "+TODO=\"APPLIED\"" ((org-agenda-overriding-header "Applied — awaiting reply")))
        (tags-todo "+TODO=\"INTERVIEW\"" ((org-agenda-overriding-header "Interviews")))
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
    (local-set-key (kbd "C-c j o") #'cv-jobs-open)))

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
                   "and never duplicate an existing lead. When you identify a "
                   "strong lead, append (never rewrite) a new entry under the "
                   "'Leads' heading in exactly this Org format:\n"
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

(unless cv-jobs--loaded
  (setq cv-jobs--loaded t)
  (cv-jobs--setup-captures)
  (cv-jobs--setup-agenda)
  (add-hook 'org-mode-hook #'cv-jobs--setup-buffer)
  (add-hook 'org-capture-after-finalize-hook #'cv-jobs--maybe-auto-commit)
  (with-eval-after-load 'gptel (cv-jobs--setup-gptel))
  ;; `org-mode-hook' already ran for the buffer that triggered this load,
  ;; so configure it directly as well.
  (cv-jobs--setup-buffer))

(provide 'jobsearch)
;;; jobsearch.el ends here
