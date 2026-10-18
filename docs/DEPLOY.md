# Deployment, getting a clickable link

Two accounts are needed, and both require a human because both are behind
interactive sign-up or OAuth. The application itself is ready: a clean
`pip install -r requirements.txt` build was verified to import the package, run
every query against a live FalkorDB, and boot with configuration supplied only
through the environment.

Budget about ten minutes total.

---

## A. FalkorDB Cloud (the database)

The app needs a FalkorDB it can reach. Local Docker is not available to a hosted
Streamlit app, so this is a free cloud instance.

1. Sign in or sign up at <https://app.falkordb.cloud/signup>.
   - **If you signed up with Google**, email/password login will not work for
     scripting. Use the console UI instead, that is fine, everything below is
     doable by clicking.
2. **Create an instance.**
   - Tier: **Free**
   - Provider / region: AWS `us-east-1` is a safe default
   - Name: `immunegraph`
   - Set a **database password**, this is separate from your account password.
     Store it somewhere; it goes into the app config, not into git.
3. Wait until the instance shows **RUNNING** (a minute or two).
4. Copy four values from the instance page:

   | Value | Notes |
   | --- | --- |
   | **Host** | looks like `something.us-east-1.falkordb.cloud` |
   | **Port** | usually a five-digit number |
   | **Username** | usually `falkordb` |
   | **Password** | the database password you set |

> **Free-tier caveats, straight from FalkorDB's docs.** 100 MB RAM. **No
> persistence and no backups.** Instances are **stopped after 1 day idle** and
> **deleted after 7 days** of inactivity. No TLS.
>
> Two consequences. First, that is exactly why ImmuneGraph keeps a write-ahead
> log and can rebuild itself, but §B note 3 matters for surviving a cold start.
> Second, **load the app shortly before judging** so the instance is awake, or
> the first judge to click waits on a cold database.

---

## B. Streamlit Community Cloud (the app)

1. Go to <https://share.streamlit.io> and click **Continue with GitHub**.
   Authorise it to read your repositories. This is the step that cannot be
   scripted.
2. **Create app** → **Deploy a public app from GitHub**, then:
   - Repository: `TCPCore/immunity`
   - Branch: `main`
   - Main file path: `app.py`
3. Open **Advanced settings → Secrets** and paste this, with your own values:

   ```toml
   FALKORDB_HOST = "your-instance.us-east-1.falkordb.cloud"
   FALKORDB_PORT = "12345"
   FALKORDB_USER = "falkordb"
   FALKORDB_PASSWORD = "your-database-password"
   FALKORDB_TLS = "false"
   FALKORDB_GRAPH = "immunegraph"

   # No LLM key needed: the deterministic extractor keeps every graph query real.
   EXTRACTOR = "rule"

   # Cold starts: rebuild the Mars run from the committed write-ahead log.
   SEED_LOG = "data/seed_writes.jsonl"
   WRITE_LOG_PATH = "/tmp/immunegraph-writes.db"
   ```

   `config.py` bridges `st.secrets` into the environment automatically, so no
   code change is needed. Secrets are **not** in the repository and must not be.
4. Deploy. First build takes a couple of minutes.

---

## C. Verify, then hand back

Once the app is up, two checks are worth running:

1. **From the app:** press **▶ Run Mars mission**. The cascade should build, an
   infection should appear with severity 6.0, the antibody should cite NASA and
   register a kernel fact, and **♻️ Rebuild from log** should report the rebuilt
   graph as *identical*.
2. **From a terminal, against the real cloud instance**, this is the check that
   the deployed environment genuinely works, not just locally:

   ```bash
   cd immunity
   cat > .env <<'EOF'
   FALKORDB_HOST=your-instance.us-east-1.falkordb.cloud
   FALKORDB_PORT=12345
   FALKORDB_USER=falkordb
   FALKORDB_PASSWORD=your-database-password
   EXTRACTOR=rule
   EOF
   .venv/bin/python -m immunegraph.verify_queries
   ```

   It must end with *"All non-optional queries executed and returned expected
   shapes."* If a query fails here, it would have failed for a judge.

`.env` is gitignored. Never commit it, and never paste real credentials into a
tracked file.

---

## D. Security

- Treat the database password like a production secret.
- **Rotate any password that has been shared in a chat or ticket.** Assume it is
  compromised the moment it is typed somewhere it can be read back.
- If a credential is ever committed by accident, rotate it first and clean
  history second, rewriting history does not un-leak a secret.

---

## Checklist

- [ ] FalkorDB Cloud free instance is **RUNNING**
- [ ] Streamlit app is deployed and public
- [ ] Secrets set in Streamlit, **not** in the repo
- [ ] `verify_queries` passes against the **cloud** instance
- [ ] "Run Mars mission" works on the deployed URL
- [ ] "Rebuild from log" reports an identical graph
- [ ] Link added to `README.md` (top, above the fold)
- [ ] Instance pinged shortly before judging so it is not asleep
