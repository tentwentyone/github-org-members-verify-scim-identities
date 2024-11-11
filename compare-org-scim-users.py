import datetime
import logging
import os
import jwt
import requests
import argparse
import base64
from rich import print
from rich.logging import RichHandler
from rich.table import Table
from rich import box
from rich.console import Console
from google.cloud import secretmanager_v1
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import sys


class GHWrapper:
    # This class is a wrapper around the GitHub REST API and GitHub GraphQL API to list enterprise members (GraphQL) and to check SCIM identities (REST API)
    # The class uses the GitHub App authentication method to get a token with organization permissions to list the SCIM identities
    # The class uses the GitHub Personal Access Token (PAT) authentication method to list the organization members (because it's at Enterpris level)

    # Github token permissions required from GitHub App:
    #   Organization:
    #       Administration: Read
    #       members: read
    #   Repository: (this allows us to install the app on a specific repository)
    #       Metadata: read

    # To get the token, you need to set the following environment variables:
    #   GH_APP_ID: the GitHub App ID
    #   GH_PEM_KEY_PATH or GH_PEM_KEY: the path to the private key or the private key itself encoded in base64
    #   GH_INSTALL_ID: the GitHub App installation ID
    #   GH_ORG: the organization name

    # Github token permissions required from GitHub PAT (Personal Access Token) at Enterprise level:
    #   Organization:
    #       Administration: Read
    #       members: read


    def __init__(self, app_id, pem_key_path, install_id, org, pat_token, pem_key=None):
        self.app_id = app_id
        self.install_id = install_id
        self.pem_key_path = pem_key_path
        self.pem_key = pem_key
        self.token = self.get_gh_token()
        self.org = org
        self.pat_token = pat_token

    def get_gh_token(self):
        """
        Get a GitHub API token using the GitHub App authentication

        Returns:
            dict: with the following
                - token: the GitHub API token
                - expires_at: the expiration date of the token
                - permissions: the permissions of the token
                - repository_selection: the repository selection of the token
        """

        creds = {
            "app_id": self.app_id,
            "pem_key_path": self.pem_key_path,
            "install_id": self.install_id,
            "pem_key": self.pem_key,
        }

        # check if pem_key is defined and decode it from base64
        if creds["pem_key"] is not None:
            creds["pem_key"] = base64.b64decode(creds["pem_key"]).decode("utf-8")

        # if pem_key is not defined, check if pem_key_path is defined and read the content of the file
        elif creds["pem_key"] is None and creds["pem_key_path"] is not None:
            # check if pem_key path exists and read the content
            if os.path.exists(creds["pem_key_path"]):
                with open(creds["pem_key_path"], "r") as f:
                    creds["pem_key"] = f.read().strip()

        if len(creds["app_id"]) == 0 or len(creds["pem_key"]) == 0 or len(creds["install_id"]) == 0:
            raise Exception("GH_APP_CREDS is not set correctly")

        now = int(datetime.datetime.now().timestamp())
        payload = {
            "iat": now - 60,
            "exp": now + 60 * 8,  # expire after 8 minutes
            "iss": creds["app_id"],
        }
        encoded = jwt.encode(payload=payload, key=creds["pem_key"], algorithm="RS256")

        url = f'https://api.github.com/app/installations/{creds["install_id"]}/access_tokens'
        headers = {
            "Authorization": f"Bearer {encoded}",
        }
        response = requests.post(url, headers=headers)

        if response.status_code != 201:
            logging.error(f"[bold red]Failed to get app token from GitHub API: {response.text}", extra={"markup": True})

        # example of response content {'token': 'ghs_XXXXXXXXXXXXXXXX', 'expires_at': '2024-04-15
        if response.status_code == 201:
            logging.debug("[bold green]Successfully got GitHub API token", extra={"markup": True})
            return response.json()["token"]

    def list_org_scim_identities(self):
        """
        Get a list of SCIM provisioned identities' GitHub handles

        Returns:
            list: A list of GitHub handles (userNames) of the SCIM provisioned identities
        """
        url = f'https://api.github.com/scim/v2/organizations/{self.org}/Users'
        headers = {
            "Authorization": f"Bearer {self.token}", # Uses the GitHub app token
            "Accept": "application/scim+json",
        }
        params = {
            "startIndex": 1,
            "count": 100
        }
        all_handles = []

        while True:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                logging.error(
                    f"[bold red]Failed to list SCIM organization identities: {response.text}",
                    extra={"markup": True}
                )
                break

            data = response.json()

            if not data:
                logging.error(
                    f"[bold red]Failed to get response from SCIM organization identities json: {response.text}",
                    extra={"markup": True}
                )                
                break

            resources = data.get("Resources", [])
            if not resources:
                logging.error(
                    f"[bold red]Failed to get Resources block from SCIM organization identities json: {response.text}",
                    extra={"markup": True}
                )  
                break

            handles = [resource["userName"] for resource in resources if "userName" in resource]
            all_handles.extend(handles)
            params["startIndex"] += params["count"]

            if params["startIndex"] > data["totalResults"]:
                break

        return all_handles

    def list_org_verified_emails(self):
        """
        Get a list of the users emails in a specific GitHub organization

        Returns:
            list: A list of the organization verified e-mails of the users in the organization
        """
        # Select your transport with a defined url endpoint
        # Uses the GitHub PAT token
        transport = RequestsHTTPTransport(url="https://api.github.com/graphql", headers={"Authorization": f"Bearer {self.pat_token}"})

        # Create a GraphQL client using the defined transport
        client = Client(transport=transport, fetch_schema_from_transport=True)

        # Provide a GraphQL query
        query = gql(
            """
            query ($org: String!) {
                organization(login: $org) {
                    membersWithRole(first: 100) {
                        edges {
                            cursor
                            node{
                            login
                            name
                            organizationVerifiedDomainEmails(login: $org)
                            createdAt
                            url
                            }
                        }
                    }
                }
            }
        """
        )

        variables = {"org": "nosportugal"}

        # Execute the query on the transport
        result = client.execute(query, variable_values=variables)

        # Loop through all pages
        while result["organization"]["membersWithRole"]["edges"]:
            cursor = ""
            for user in result["organization"]["membersWithRole"]["edges"]:
                cursor = user["cursor"]
                login = user["node"]["login"]
                name = user["node"]["name"]
                emails = str(user["node"]["organizationVerifiedDomainEmails"]).strip("[]").replace("'", "")
                date_created = user["node"]["createdAt"]
                url = user["node"]["url"]

                print(f"Login: {login}, Name: {name}, E-mails: {emails}, Date Created: {date_created}, URL: {url}")

            query = gql(
                """
                query ($org: String!, $cursor: String!) {
                organization(login: $org) {
                    membersWithRole(first: 100, after: $cursor) {
                        edges {
                            cursor
                            node{
                                login
                                name
                                organizationVerifiedDomainEmails(login: $org)
                                createdAt
                                url
                            }
                        }
                    }
                }
                }
            """
            )

            variables = {"org": "nosportugal", "cursor": cursor}

            # Execute the query on the transport
            result = client.execute(query, variable_values=variables)
 


def parse_command_line_args(args_0=sys.argv[1:]):
    arg_parser = argparse.ArgumentParser(
        description="Find all the users that do not have a SCIM ID in the organization"
    )
    arg_parser.add_argument("-o", "--out-format", type=str, default="table", help="Output format: table or txt")
    arg_parser.add_argument("--no-color", action="store_true", help="Disable color output")

    return args


if __name__ == "__main__":
    args = parse_command_line_args()

    FORMAT = "%(message)s"
    console = Console(force_terminal=True, color_system="auto" if args.no_color is False else None)

    logging.basicConfig(
        level=logging.INFO if os.getenv("RUNNER_DEBUG") != "1" else logging.DEBUG,
        format=FORMAT,
        datefmt="[%X]",
        handlers=[RichHandler(console=console, markup=True)],
    )

    logging.debug(f"Arguments: {args}")

    gh = GHWrapper(
        app_id=os.getenv("GH_APP_ID"),
        pem_key_path=os.getenv("GH_PEM_KEY_PATH"),
        pem_key=os.getenv("GH_PEM_KEY"),
        install_id=os.getenv("GH_INSTALL_ID"),
        org=os.getenv("GH_ORG"),
        pat_token=os.getenv("GH_PAT_TOKEN"),
    )

    enterprise_members = gh.list_org_verified_emails()
    scim_identities = gh.list_org_scim_identities()


