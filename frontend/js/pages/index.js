import { getSessionUser } from "../auth.js";

// Landing page: send people where they belong.
location.replace((await getSessionUser()) ? "app.html" : "login.html");
