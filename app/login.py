"""Self-contained login page (no /ui assets — those are behind the gate)."""

LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>InnerOracle</title>
<style>
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; color:#fff;
         background:linear-gradient(160deg,#b9764e,#6e4636);
         background-attachment:fixed; padding:20px; }
  .card { width:100%; max-width:360px; padding:30px 26px; border-radius:20px;
          background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.18);
          backdrop-filter:blur(22px) saturate(1.3); -webkit-backdrop-filter:blur(22px) saturate(1.3);
          box-shadow:0 16px 50px rgba(0,0,0,.30); text-align:center; }
  h1 { margin:0 0 4px; font-family:Georgia,serif; font-size:24px; }
  p { margin:0 0 20px; color:rgba(255,255,255,.65); font-size:14px; }
  input { width:100%; padding:13px 15px; border-radius:13px; border:1px solid rgba(255,255,255,.2);
          background:rgba(255,255,255,.10); color:#fff; font-size:15px; outline:none; }
  input::placeholder { color:rgba(255,255,255,.5); }
  input:focus { border-color:#f0a878; box-shadow:0 0 0 3px rgba(240,168,120,.2); }
  button { width:100%; margin-top:12px; padding:13px; border:none; border-radius:13px; cursor:pointer;
           background:#f0a878; color:#3a2114; font-size:15px; font-weight:600; }
  button:hover { filter:brightness(1.06); }
  .err { margin-top:14px; font-size:13px; color:#ffd9c2; min-height:16px; }
</style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>🪔 InnerOracle</h1>
    <p>Enter your access token to continue.</p>
    <input type="password" name="token" placeholder="Access token" autofocus autocomplete="current-password" />
    <input type="hidden" name="next" value="{{NEXT}}" />
    <button type="submit">Enter</button>
    <div class="err" id="err"></div>
  </form>
  <script>
    if (new URLSearchParams(location.search).get('error'))
      document.getElementById('err').textContent = 'Incorrect token — try again.';
  </script>
</body>
</html>"""
