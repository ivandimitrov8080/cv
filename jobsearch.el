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
;; Three gptel presets are registered here:
;;
;;   job         the supervisor (`@job'); its only tool is gptel-agent's
;;               `Agent' tool, so it answers questions and delegates.
;;   job-search  defined in agents/job-search.org -- finds new leads.
;;   job-apply   defined in agents/job-apply.org -- drives a browser to apply.
;;
;; The latter two are also sub-agents of `job'.  They are defined once, as
;; gptel-agent files, and each is registered both as an agent (for the `Agent'
;; tool) and as an ordinary preset (so `@job-search' / `@job-apply' still work
;; directly).  Nothing here modifies the global `job-search' preset.

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
                          (t (format "C-c c => %s" bind))))
         (agent-tool (ignore-errors (gptel-get-tool "Agent")))
         (agent-enum
          (when agent-tool
            (plist-get
             (seq-find (lambda (arg) (equal (plist-get arg :name) "subagent_type"))
                       (gptel-tool-args agent-tool))
             :enum))))
    (message (concat "jobsearch: %s | %s | capture keys: %s | "
                     "jobs.org in agenda: %s | gptel-agent: %s | "
                     "agents: %s | Agent enum: %s")
             (if (featurep 'jobsearch) "loaded" "NOT LOADED")
             bind-desc
             (mapcar #'car (seq-filter #'listp org-capture-templates))
             (if (member cv-jobs-file org-agenda-files) "yes" "no")
             (if (featurep 'gptel-agent) "loaded" "NOT LOADED")
             (mapcar #'car (bound-and-true-p gptel-agent--agents))
             (if agent-enum (seq-into agent-enum 'list) "n/a"))))

(defun cv-jobs-setup ()
  "(Re)install the job-search captures, agenda, keys and gptel wiring.

Safe to call repeatedly; the capture templates this file adds are refreshed
in place and the hooks and agenda entries are idempotent.  The sub-agent
files are re-parsed too, so editing agents/*.org only needs `cv-jobs-reload'."
  (interactive)
  (cv-jobs--setup-captures)
  (cv-jobs--setup-agenda)
  (add-hook 'org-mode-hook #'cv-jobs--setup-buffer)
  (add-hook 'org-after-todo-state-change-hook #'cv-jobs--record-applied-date)
  ;; `org-mode-hook' may already have run for the triggering buffer.
  (cv-jobs--setup-buffer)
  (cv-jobs--install-subagents)
  (setq cv-jobs--loaded t))

(setq org-modern-todo-faces
      '(("LEAD" . "⟳") ("APPLIED" . "⇒") ("INTERVIEW" . "★")
        ("OFFER" . "★") ("HIRED" . "✔") ("REJECTED" . "✘")))

(setq org-export-with-properties t)

;; The `job-search' preset used to be defined here.  It now lives in
;; agents/job-search.org and is registered by `cv-jobs--install-subagents',
;; called from `cv-jobs-setup'.

;;; Applying to leads (Playwright MCP)

;; The `job-apply' preset turns the browser into the applicant: it fetches
;; the next LEAD with the `cv_jobs_next_lead' tool, then drives a real browser
;; through the Playwright MCP server to fill in, and submit, the application.
;; Its definition lives in agents/job-apply.org; this section only provides the
;; emacs-side tools it calls and `cv-jobs--connect-playwright', the function its
;; `:PRE' property names.
;;
;; The `playwright-mcp' binary comes from the project dev shell (see
;; flake.nix), so Emacs has to inherit the dev-shell environment (e.g. be
;; started from `~/src/cv' under direnv) for it to be on PATH.

(require 'mcp)
(require 'gptel-integrations)

;; Soft require: everything else in this file (capture, agenda, the cv_jobs_*
;; tools) has nothing to do with gptel-agent, so a stale Emacs image without it
;; must not take the job-search tracker down with it.  `cv-jobs-verify' says
;; whether the sub-agent layer is up.
(require 'gptel-agent nil t)

;;; Sub-agents

;; `job-search' and `job-apply' are defined as gptel-agent files under
;; agents/, not as `gptel-make-preset' forms here.  gptel-agent parses them,
;; rebuilds the `subagent_type' enum of its `Agent' tool from whatever it
;; finds, and `cv-jobs--install-subagents' republishes each one as an ordinary
;; preset -- so `@job-search' / `@job-apply' behave exactly as before while a
;; master preset can also spawn them as sub-agents.
;;
;; Pointing `gptel-agent-dirs' at our directory *and only ours* is what keeps
;; gptel-agent's bundled `executor' / `researcher' / `introspector' agents out
;; of the enum; it also means the `gptel-agent' and `gptel-plan' presets (and
;; their Bash / Eval / Write / Edit toolset) are never defined at all.

;; NOTE: gptel-agent's Org property parser runs `intern' on the result of
;; `read', so `:PARENTS:' must be written as a list of *strings*:
;;
;;   :PARENTS: ("job-search")
;;
;; The more natural-looking `:PARENTS: (job-search)' reads as the symbol
;; `job-search' and makes `gptel-agent-update' signal
;; `(wrong-type-argument stringp job-search)' before any agent is
;; registered.  Do not drop the quotes in agents/*.org.

(defconst cv-jobs-agent-directory (expand-file-name "agents" cv-jobs-directory)
  "Directory of the project's gptel-agent sub-agent definitions.")

(defconst cv-jobs-agent-names '("job-search" "job-apply")
  "Agents under `cv-jobs-agent-directory' that are also gptel presets.

Names are the file base names, which is what gptel-agent defaults to when an
agent file has no `name' property.")

(setq gptel-agent-dirs (list cv-jobs-agent-directory))

(defun cv-jobs--connect-playwright ()
  "Start the Playwright MCP server used by the `job-apply' agent.

The `playwright-mcp' binary comes from the project dev shell (see
flake.nix), so Emacs has to inherit the dev-shell environment (e.g. be
started from `~/src/cv' under direnv) for it to be on PATH.

This is the `:PRE' property of agents/job-apply.org.  It lives here because
an Org property value has to be a single readable form, and a lambda would
be unreadable inside a property drawer."
  (add-to-list 'mcp-hub-servers
               '("playwright" . (:command "playwright-mcp"
                                :args ("--executable-path"
                                       "/home/ivand/src/cv/.devenv/profile/bin/chromium-sandboxed"))))
  (gptel-mcp-connect '("playwright") t))

(defun cv-jobs--install-subagents ()
  "Parse `cv-jobs-agent-directory' and register each agent as a preset.

`gptel-agent-update' re-reads the agent files and rebuilds the enum of the
`Agent' tool; each agent is then published under its own name.  Re-running is
idempotent: `gptel-make-preset' replaces the spec of an existing preset."
  (if (not (fboundp 'gptel-agent-update))
      (message "cv-jobs: gptel-agent is not available; the `job' supervisor \
and its sub-agents are disabled")
    (gptel-agent-update)
    ;; `gptel-agent-update' registers *every* regular file in the directory and
    ;; does not check the extension, so editor droppings -- undo-tree history
    ;; (`undo-tree-auto-save-history' is on globally), `#file#', `.file.swp' --
    ;; end up in `gptel-agent--agents' and in the `subagent_type' enum.  Keep
    ;; only the agents this project actually ships.
    (setq gptel-agent--agents
          (seq-filter (lambda (entry) (member (car entry) cv-jobs-agent-names))
                      gptel-agent--agents))
    ;; Rebuild the enum from the pruned list.  This mirrors what
    ;; `gptel-agent-update' does a few lines earlier; gptel-agent offers no
    ;; public entry point for it, so it has to be spelled out here.
    (setf (plist-get (car (gptel-tool-args (gptel-get-tool "Agent"))) :enum)
          (vconcat (mapcar #'car gptel-agent--agents)))
    (dolist (name cv-jobs-agent-names)
      (when-let* ((spec (cdr (assoc name gptel-agent--agents))))
        (apply #'gptel-make-preset (intern name) spec)))))

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

;;; The supervisor

;; `job' is the preset you address as `@job'.  It has exactly one tool, the
;; `Agent' tool that gptel-agent provides, so all it can do with the outside
;; world is delegate to `job-search' or `job-apply'.  It deliberately has no
;; file or shell tools of its own: the pipeline is the sub-agents' business.

(defun cv-jobs--agent-roster ()
  "Return a bulleted list of the available sub-agents, for the `job' prompt."
  (mapconcat
   (lambda (name)
     (format "- `%s`: %s" name
             (or (plist-get (cdr (assoc name (bound-and-true-p gptel-agent--agents)))
                            :description)
                 "(no description)")))
   cv-jobs-agent-names "\n"))

(gptel-make-preset 'job
  :description "Job-search supervisor: answers questions and delegates to sub-agents."
  :backend "Deepseek"
  :model 'deepseek-v4-flash
  :use-tools t
  :tools '("Agent")
  :system
  (lambda ()
    (concat
     "You are the job-search supervisor for the CV project in ~/src/cv.

You have one tool, `Agent', which runs a specialised sub-agent to completion
and returns its report.  Available sub-agents:

"
     (cv-jobs--agent-roster)
     "

Rules:
- Answer directly from what you already know.  Only delegate work that
  genuinely needs a tool.
- Sub-agents share no context with this conversation: they see only the
  `prompt' you pass them.  Every brief must be self-contained -- name the
  company, the URL, which lead, and exactly what to report back.  Never write
  \"the one we discussed\".
- Delegate a whole browser application run to `job-apply'.  Do not try to
  drive the pipeline yourself: `job-apply' owns the cv_jobs_* tools and the
  per-run state behind them.
- Never edit jobs.org directly and never invent an answer for an application
  form.  If a required field is not in the CV, the sub-agent archives the lead
  instead of guessing.
- Report what the sub-agent returned.  Do not hand the task back to the user.")))

;; The setup call lives here rather than next to `cv-jobs-setup' because the
;; function now registers the sub-agents, and `cv-jobs--install-subagents'
;; (and gptel-agent itself) are only defined further down this file.
(cv-jobs-setup)

(provide 'jobsearch)
;;; jobsearch.el ends here
