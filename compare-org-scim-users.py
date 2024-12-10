import logging
import os
import requests
import argparse
from rich import print
from rich.logging import RichHandler
from rich.table import Table
from rich import box
from rich.console import Console
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport
import sys
from gql.transport.requests import log as requests_logger
from typing import List , Tuple
import datetime
import base64
import jwt

requests_logger.setLevel(logging.WARNING)

class GHWrapper:
    # This class is a wrapper around the GitHub REST API and GitHub GraphQL API to list enterprise members (GraphQL) and to check SCIM identities (REST API)

    # Github token permissions required from GitHub App:
    #   Organization:
    #       Administration: Read
    #       members: read



    def __init__(self, app_id, pem_key_path, install_id, org, pem_key=None):
        if not org:
            raise ValueError("Organization is required")

        self.app_id = app_id
        self.install_id = install_id
        self.pem_key_path = pem_key_path
        self.pem_key = pem_key
        self.token = self.get_gh_token()
        self.org = org

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

            #check if pem_key is base64 encoded
            if creds["pem_key"].startswith("LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQ"):
                logging.debug("pem key from encoded in base64")
                creds["pem_key"] = base64.b64decode(creds["pem_key"]).decode("utf-8")
            else:
                logging.debug("pem key is in clear text")

            #check if pem_key is a valid RSA key by checking the start and end of the key
            if not creds["pem_key"].startswith("-----BEGIN RSA PRIVATE KEY-----") or not creds["pem_key"].endswith("-----END RSA PRIVATE KEY-----"):
                raise Exception("GH_PEM_KEY is not a valid RSA key")

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
            list: A list of GitHub handles (emails) of the SCIM provisioned identities
        """
        url = f'https://api.github.com/scim/v2/organizations/{self.org}/Users'
        headers = {
            "Authorization": f"Bearer {self.token}", # Uses the GitHub app token
            "Accept": "application/scim+json",
            "X-GitHub-Api-Version": "2022-11-28",
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

            handles = [resource["userName"].lower() for resource in resources if "userName" in resource]
            all_handles.extend(handles)
            params["startIndex"] += params["count"]

            if params["startIndex"] > data["totalResults"]:
                break

        return all_handles

    def list_org_verified_emails(self) -> Tuple[dict, List]:
        """
        Get organization users' verified emails.

        Returns:
            Tuple[dict, List]: A tuple with the following:
                - A dict with the users and their verified emails
                - A list of users without verified emails
        """
        # Select your transport with a defined url endpoint
        transport = RequestsHTTPTransport(url="https://api.github.com/graphql", headers={"Authorization": f"Bearer {self.token}"})

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

        variables = {"org": os.getenv("GH_ORG")}

        # Execute the query on the transport
        result = client.execute(query, variable_values=variables)

        users = {
                #   "user1" : ["email1", "email2"],
        }
        users_without_verified_email = []

        # Loop through all pages
        while result["organization"]["membersWithRole"]["edges"]:
            cursor = ""
            for user in result["organization"]["membersWithRole"]["edges"]:
                cursor = user["cursor"]
                login = user["node"]["login"]
                name = user["node"]["name"]
                emails = user["node"]["organizationVerifiedDomainEmails"]
                date_created = user["node"]["createdAt"]
                url = user["node"]["url"]


                logging.debug(f"Login: {login}, Name: {name}, E-mails: {emails}, Date Created: {date_created}, URL: {url}")

                emails = [email.lower() for email in emails] # Lowercase all emails
                if len(emails) > 0: # Only add users with verified emails
                    users[login] = emails
                else:
                    logging.info(f"[bold yellow]User {login} has no verified e-mails", extra={"markup": True})
                    users_without_verified_email.append(login)

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

            variables = {"org": os.getenv("GH_ORG"), "cursor": cursor}

            # Execute the query on the transport
            result = client.execute(query, variable_values=variables)

        return users , users_without_verified_email


def parse_command_line_args(args_0=sys.argv[1:]):
    arg_parser = argparse.ArgumentParser(
        description="Find all the users that do not have a SCIM ID in the organization"
    )
    arg_parser.add_argument("-o", "--out-format", type=str, default="table", help="Output format: table or txt")
    arg_parser.add_argument("--no-color", action="store_true", help="Disable color output")

    args = arg_parser.parse_args(args_0)

    return args

# Add validation for environment variables
def validate_environment():
    required_vars = ['GH_ORG', 'GH_APP_ID', 'GH_INSTALL_ID']
    pem_key_vars = ['GH_PEM_KEY_PATH', 'GH_PEM_KEY']

    missing = [var for var in required_vars if not os.getenv(var)]

    if not any(os.getenv(var) for var in pem_key_vars):
        missing.extend(pem_key_vars)

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

def main():


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

    validate_environment()

    gh = GHWrapper(
        app_id=os.getenv("GH_APP_ID"),
        pem_key_path=os.getenv("GH_PEM_KEY_PATH"),
        pem_key=os.getenv("GH_PEM_KEY"),
        install_id=os.getenv("GH_INSTALL_ID"),
        org=os.getenv("GH_ORG"),
    )

    enterprise_members, unverified_members  = gh.list_org_verified_emails()
    scim_identities = gh.list_org_scim_identities()

    scim_identities.sort()

    # Find the users that are in the enterprise members list but not in the scim identities list

    users_not_in_scim = {}

    for user in enterprise_members:
        present = False
        for email in enterprise_members[user]:
            if email in scim_identities:
                present = True
                break
        if not present:
            users_not_in_scim[user] = enterprise_members[user]

    # Print the results
    print_results(args, console, unverified_members, users_not_in_scim)

    if "GITHUB_STEP_SUMMARY" in os.environ:
        with console.capture() as capture:
            print_results(args, console, unverified_members, users_not_in_scim)
        output = capture.get()
        with open(os.getenv("GITHUB_STEP_SUMMARY"), "a") as f:
            print(output, file=f)

def print_results(args, console, unverified_members, users_not_in_scim):
    if args.out_format == "table":
        if len(users_not_in_scim) > 0:
            console.print("## Users without SCIM ID", style="bold red")
            table = Table(title="", box=box.MARKDOWN)
            table.add_column("User", style="bold")
            table.add_column("E-mails", style="bold")
            table.add_column("Org link", style="bold")
            for user in users_not_in_scim:
                table.add_row(str(user), " , ".join(users_not_in_scim[user]), f"https://github.com/orgs/{os.getenv('GH_ORG')}/people/{str(user)}/sso", style="yellow")
            console.print(table)
        else:
            console.print("## All users have SCIM ID", style="bold green")

        if len(unverified_members) > 0:
            console.print("## Users without verified e-mails", style="bold red")
            table2 = Table(title="", box=box.MARKDOWN)
            table2.add_column("Username", style="bold")
            table2.add_column("Org link", style="bold")
            for user in unverified_members:
                table2.add_row(str(user),f"https://github.com/orgs/{os.getenv('GH_ORG')}/people/{str(user)}/sso", style="yellow")
                console.print(table2)
        else:
            console.print("## All users have verified e-mails", style="bold green")




    elif args.out_format == "txt":
        if len(users_not_in_scim) > 0:
            console.print("Users without SCIM ID:")
            for user in users_not_in_scim:
                console.print(f"\t{user} : {users_not_in_scim[user]}")
        else:
            console.print("All users have SCIM ID")

        if len(unverified_members) > 0:
            console.print("Users without verified e-mails:")
            for user in unverified_members:
                console.print(f"\t{user}")
        else:
            console.print("All users have verified e-mails")




if __name__ == "__main__":
    main()