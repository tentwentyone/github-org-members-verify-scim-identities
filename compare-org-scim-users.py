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
from typing import List

requests_logger.setLevel(logging.WARNING)

class GHWrapper:
    # This class is a wrapper around the GitHub REST API and GitHub GraphQL API to list enterprise members (GraphQL) and to check SCIM identities (REST API)
    # The class uses the GitHub Personal Access Token (PAT) authentication method to list the organization members (because it's at Enterpris level) and to list the SCIM identities

    # Github token permissions required from GitHub App:
    #   Organization:
    #       Administration: Read
    #       members: read
    #   Repository: (this allows us to install the app on a specific repository)
    #       Metadata: read

    # Github token permissions required from GitHub PAT (Personal Access Token) at Enterprise level:
    #   Organization:
    #       Administration: Read
    #       members: read



    def __init__(self, org, pat_token):
        if not org or not pat_token:
            raise ValueError("Organization and PAT token are required")
        self.org = org
        self.pat_token = pat_token
        self.session = requests.Session()  # Reuse connection
        self.session.headers.update({
            "Authorization": f"Bearer {pat_token}",
            "Accept": "application/scim+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })

    def list_org_scim_identities(self):
        """
        Get a list of SCIM provisioned identities' GitHub handles

        Returns:
            list: A list of GitHub handles (emails) of the SCIM provisioned identities
        """
        url = f'https://api.github.com/scim/v2/organizations/{self.org}/Users'
        headers = {
            "Authorization": f"Bearer {self.pat_token}", # Uses the GitHub app token
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

    def list_org_verified_emails(self) -> List[List[str]]:
        """
        Get organization users' verified emails.

        Returns:
            List[List[str]]: List of email lists for each user
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

        variables = {"org": os.getenv("GH_ORG")}

        # Execute the query on the transport
        result = client.execute(query, variable_values=variables)


        all_emails = []

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
                    all_emails.append(emails)
                else:
                    logging.warning(f"[bold yellow]User {login} has no verified e-mails", extra={"markup": True})

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

        return all_emails


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
    required_vars = ['GH_ORG', 'GH_PAT_TOKEN', ]
    missing = [var for var in required_vars if not os.getenv(var)]
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
        pat_token=os.getenv("GH_PAT_TOKEN"),
        org=os.getenv("GH_ORG")
    )

    enterprise_members = gh.list_org_verified_emails()
    scim_identities = gh.list_org_scim_identities()

    enterprise_members.sort()
    scim_identities.sort()


    # Find the users that are in the enterprise members list but not in the scim identities list

    users_not_in_scim = []

    for user in enterprise_members:
        present = False
        for email in user:
            if email in scim_identities:
                present = True
                break
        if not present:
            users_not_in_scim.append(user)

    # Print the results

    if args.out_format == "table":
        table = Table(title="Users without SCIM ID", box=box.SIMPLE)
        table.add_column("User", style="bold")
        for user in users_not_in_scim:
            table.add_row(str(user))
        console.print(table)

    else:
        print("Users without SCIM ID:")
        for user in users_not_in_scim:
            print(str(user))



if __name__ == "__main__":
    main()