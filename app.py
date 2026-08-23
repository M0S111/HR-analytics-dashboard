import dash
from dash import dcc, html, Input, Output, State
from flask_caching import Cache
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import asyncio
import httpx
import os

# Adzuna API Credentials
APP_ID = os.environ.get("APP_ID", "")
APP_KEY = os.environ.get("APP_KEY", "")
MAX_CHARS = 20

app = dash.Dash(__name__)
server = app.server

cache = Cache(app.server, config={
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_THRESHOLD": 100
})

app.layout = html.Div([
    html.H1("HR & Job Market Analytics Dashboard", style={"textAlign": "center"}),
    
    # Control Panel
    html.Div([
        html.Label("Search Role: "),
        dcc.Input(id="search-term", type="text", value="Data Analyst", style={"margin": "10px"}),
        html.Label("Country: "),
        dcc.Dropdown(
            id="search-country",
            options=[
                {"label": "United States", "value": "us"},
                {"label": "United Kingdom", "value": "gb"},
                {"label": "Canada", "value": "ca"}
            ],
            value="us",
            clearable=False,
            style={"width": "300px", "margin": "10px"}
        ),
        html.Label("City/State: "),
        dcc.Input(id="search-city", type="text", value="", style={"margin": "10px"}),
        html.Button("Refresh Data", id="refresh-btn", n_clicks=0, style={"cursor": "pointer"}),
    ], style={"padding": "20px", "backgroundColor": "#f8f9fa", "borderRadius": "8px", "marginBottom": "20px"}),
    
    # Status Message
    dcc.Loading(
        id="loading-text",
        type="dot",
        color="#8473F2",
        children=html.Div(id="status-msg", style={"fontWeight": "bold", "marginBottom": "15px"}),
    ),
    
    # Dashboard Visualizations
    dcc.Loading(
        id="loading-graphs",
        type="dot",
        color="#8473F2",
        children=html.Div([
            dcc.Graph(id="mean_card", style={"width": "50%", "display": "inline-block"}),
            dcc.Graph(id="median_card", style={"width": "50%", "display": "inline-block"}),
            dcc.Graph(id="salary-dist-graph", style={"width": "100%", "display": "inline-block"}),
            dcc.Graph(id="job-titles-graph", style={"width": "100%", "display": "inline-block"}),
            dcc.Graph(id="jobs-distribution-graph", style={"width": "100%", "display": "inline-block"}),
        ])
    )
], style={"font-family": "Consolas"})


async def fetch_page(client: httpx.AsyncClient, search_country: str, search_term: str, search_city: str, page: int):
    url = f"https://api.adzuna.com/v1/api/jobs/{search_country}/search/{page}"
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "what": search_term,
        "where": search_city,
        "results_per_page": 50,
        "content-type": "application/json"
    }
    try:
        response = await client.get(url, params=params, timeout=10.0)
        if response.status_code == 200:
            return response.json().get("results", [])
        print(f"Adzuna Response Status: {response.status_code}")
        return []
    except Exception as e:
        print(f"Exception during HTTP request: {e}")
        return []


async def fetch_all_pages_async(country: str, search_term: str, search_city: str, max_pages: int):
    limits = httpx.Limits(max_connections=5)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DashApp/1.0"}

    print("\n--- [API DEBUG START] ---")
    print(f"ADZUNA_APP_ID loaded: '{APP_ID}' (Length: {len(APP_ID)})")
    print(f"ADZUNA_APP_KEY loaded: '{'SET' if APP_KEY else 'EMPTY'}' (Length: {len(APP_KEY)})")

    if not APP_ID or not APP_KEY:
        print("ERROR: Credentials missing or empty!")
        print("--- [API DEBUG END] ---\n")
        return []

    print(f"Sending GET request with role='{search_term}', location='{country}'...")
    async with httpx.AsyncClient(limits=limits, headers=headers, timeout=10.0) as client:
        tasks = [
            fetch_page(client, country, search_term, search_city, page)
            for page in range(1, max_pages + 1)
        ]
        pages_data = await asyncio.gather(*tasks)

    flat_results = [item for page_results in pages_data for item in page_results]
    print(f"Total jobs fetched: {len(flat_results)}")
    print("--- [API DEBUG END] ---\n")
    return flat_results


@cache.memoize()
def get_cached_job_data(search_country: str, search_term: str, search_city: str, max_pages: int = 5):
    # Pure isolated event loop execution for sync WSGI workers
    return asyncio.run(
        fetch_all_pages_async(search_country, search_term, search_city, max_pages)
    )


@app.callback(
    [Output("mean_card", "figure"),
     Output("median_card", "figure"),
     Output("salary-dist-graph", "figure"),
     Output("job-titles-graph", "figure"),
     Output("jobs-distribution-graph", "figure"),
     Output("status-msg", "children")],
    [Input("refresh-btn", "n_clicks")],
    [State("search-term", "value"),
     State("search-country", "value"),
     State("search-city", "value")]
)
def fetch_and_update_dashboard(n_clicks, search_term, search_country, search_city):
    if not search_term:
        return {}, {}, {}, {}, {}, "Please enter a job title to search."

    try:
        data = get_cached_job_data(search_country, search_term, search_city, max_pages=10)
        loc_display = search_city.title() if search_city else search_country.upper()

        if not data:
            return {}, {}, {}, {}, {}, f"No job listings found for '{search_term}' in {loc_display}."

        df = pd.DataFrame(data)

        df = df.dropna(subset=["latitude", "longitude"])

        numeric_cols = ["salary_min", "salary_max", "latitude", "longitude"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["company_name"] = df["company"].apply(
            lambda x: x.get("display_name") if isinstance(x, dict) else "Unknown"
        )

        df = df[df['salary_min'] > 0]
        if df.empty:
            return {}, {}, {}, {}, {}, f"No listings with valid salary data found for '{search_term}'."

        mean_sal = df["salary_min"].mean()
        median_sal = df["salary_min"].median()
        min_sal = df["salary_min"].min()

        fig_kpi1 = go.Figure(go.Indicator(
            mode="number+delta",
            value=mean_sal,
            delta={
                "reference": min_sal,
                "relative": True,
                "valueformat": ".1%",
                "position": "bottom"
            },
            number={"prefix": "$", "valueformat": ",.0f"},
            title={"text": "Avg. Salary (relative to min)"}
        )).update_layout(height=180, margin=dict(l=20, r=20, t=40, b=20))

        fig_kpi2 = go.Figure(go.Indicator(
            mode="number+delta",
            value=median_sal,
            delta={
                "reference": min_sal,
                "relative": True,
                "valueformat": ".1%",
                "position": "bottom"
            },
            number={"prefix": "$", "valueformat": ",.0f"},
            title={"text": "Median Salary (relative to min)"}
        )).update_layout(height=180, margin=dict(l=20, r=20, t=40, b=20))

        # Plot: Salary Distribution
        fig_salary = px.histogram(
            df, x="salary_min", nbins=30,
            title=f"Salary Distribution for '{search_term}'",
            labels={"salary_min": "Min Salary ($)"},
            template="plotly_white"
        ).update_traces(
            xbins=dict(start=0)
        ).update_xaxes(
            range=[0, df["salary_min"].max() * 1.05]
        ).update_yaxes(
            title_text="# of Openings",
            ticks="outside",
            ticklen=12,
            tickcolor="rgba(0,0,0,255)",
            title_standoff=20,
            automargin=True
        )

        # Plot: Top titles
        top_titles = df["title"].value_counts().reset_index()
        top_titles.columns = ["Title", "Openings"]
        top_titles["Title"] = top_titles["Title"].apply(
            lambda name: name[:MAX_CHARS] + "..." if len(name) > MAX_CHARS else name
        )

        top_titles = top_titles.groupby("Title", as_index=False)["Openings"].sum()
        top_titles = top_titles.sort_values(by="Openings", ascending=False).head(50)

        fig_titles = px.bar(
            top_titles, x="Title", y="Openings", orientation="v",
            title=f"Top Job Post titles for '{search_term}'",
            template="plotly_white"
        ).update_yaxes(
            ticks="outside",
            ticklen=12,
            tickcolor="rgba(0,0,0,255)",
            title_standoff=20,
            automargin=True
        ).update_layout(
            xaxis={"categoryorder": "total descending", "tickangle": 45},
            width=1000
        )

        # Plot: Geographic Distribution
        plt_scope = ""
        lat = []
        lon = []
        if search_country == "us":
            plt_scope = "usa"
            lat = [24, 50]
            lon = [-125, -66]
        elif search_country == "gb":
            plt_scope = "europe"
            lat = [49, 61]
            lon = [-11, 2]
        else:
            plt_scope = "north america"
            lat = [41, 75]
            lon = [-141, -52]

        def extract_country(loc):
            if isinstance(loc, dict) and isinstance(loc.get("area"), list) and len(loc["area"]) > 0:
                return loc["area"][0]
            return "Unknown"

        df["country"] = df["location"].apply(extract_country)

        fig_dot = px.scatter_geo(
            df,
            lat="latitude",
            lon="longitude",
            size="salary_min",
            hover_data=["title", "company_name"],
            scope=plt_scope,
            title="Geographic Distribution of Openings by Salary"
        )

        fig_dot.update_geos(
            lataxis_range=lat,
            lonaxis_range=lon,
            projection_scale=1,
            showcountries=True,
            countrycolor="Gray",
            coastlinecolor="Gray"
        )

        if df['country'].iloc[0] != "Unknown" and len(loc_display) <= 3:
            loc_display = df['country'].iloc[0]

        return fig_kpi1, fig_kpi2, fig_salary, fig_titles, fig_dot, f"Data refreshed. Top {len(df)} postings in {loc_display}."

    except Exception as e:
        return {}, {}, {}, {}, {}, f"Failed to fetch data: {str(e)}"


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)