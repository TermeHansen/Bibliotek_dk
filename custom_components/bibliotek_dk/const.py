CONF_AGENCY = "agency"
CONF_BRANCH_ID = "branchId"
CONF_HOST = "host"
CONF_MUNICIPALITY = "municipality"
CONF_NAME = "name"
CONF_PINCODE = "pincode"
CONF_SHOW_DEBTS = "show_debts"
CONF_SHOW_LOANS = "show_loans"
CONF_SHOW_RESERVATIONS = "show_reservations"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_USER_ID = "user_id"
CREDITS = "J-Lindvig & TermeHansen (https://github.com/TermeHansen/Bibliotek_dk)"

DOMAIN = "bibliotek_dk"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "da,en-US;q=0.9,en;q=0.8",
    "Dnt": "1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.dk/",
}

JSON_HEADERS = {
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Content-Type": "application/json",
    'Accept': '*/*',
}

MUNICIPALITY_LOOKUP_URL = "https://api.dataforsyningen.dk/kommuner/reverse?x=LON&y=LAT"

UPDATE_INTERVAL = 60
URL_FALLBACK = "https://bibliotek.kk.dk"
URL_LOGIN = "/login"
URL_LOGIN_PAGE = "/login?current-path=/user/me/dashboard"
ICON = '''<svg fill="none" xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 35 35">
<g clip-path="url(#clip0)">
<path d="M33.42 21.64C33.0397 20.6347 32.4655 19.7139 31.73 18.93C31.0559 18.1869 30.2353 17.5911 29.32 17.18C28.7903 16.9202 28.238 16.7093 27.67 16.55C28.8958 16.0436 29.9852 15.2557 30.85 14.25C32.1307 12.6395 32.7748 10.6145 32.66 8.56005C32.7195 7.4066 32.5347 6.25358 32.1177 5.17652C31.7007 4.09945 31.0608 3.12264 30.24 2.31005C29.3502 1.50944 28.3106 0.893081 27.1812 0.496591C26.0518 0.100101 24.855 -0.0686637 23.66 4.78951e-05H0V2.43005H3.54V32.57H0V35H24.88C26.1219 35.0529 27.361 34.8423 28.5157 34.3821C29.6705 33.9219 30.7148 33.2226 31.58 32.3301C33.3112 30.3743 34.2085 27.8188 34.08 25.21C34.1048 23.9876 33.8802 22.7729 33.42 21.64ZM24.1 14.25C25.3771 12.6381 26.0176 10.6132 25.9 8.56005C25.9512 7.4655 25.7849 6.37167 25.4107 5.34179C25.0365 4.31192 24.4619 3.36645 23.72 2.56005C24.3934 2.55725 25.0603 2.69246 25.6794 2.95732C26.2986 3.22218 26.8569 3.61109 27.32 4.10005C27.7985 4.62229 28.1689 5.23403 28.41 5.90005C28.8031 6.88614 29.0034 7.93849 29 9.00005C29.0136 10.0236 28.8373 11.0408 28.48 12C28.2435 12.741 27.8514 13.4229 27.33 14C26.7903 14.5444 26.1388 14.9649 25.4205 15.2325C24.7022 15.5002 23.9343 15.6085 23.17 15.55H22.73C23.228 15.1625 23.6868 14.727 24.1 14.25ZM14.63 2.53005H16.41C17.1752 2.47082 17.9441 2.57978 18.6628 2.84927C19.3814 3.11877 20.0324 3.54226 20.57 4.09005C20.9442 4.49544 21.2514 4.95792 21.48 5.46005C22.004 6.56564 22.2674 7.77669 22.25 9.00005C22.2611 10.0966 22.0537 11.1845 21.64 12.2C21.2665 13.2589 20.5475 14.1611 19.5987 14.7616C18.65 15.362 17.5268 15.6257 16.41 15.51H14.63V2.53005ZM10.92 32.4701H7.25V18.05H10.92V32.4701ZM10.92 15.52H7.25V2.52005H10.92V15.52ZM22.58 29.66C22.3813 30.0238 22.1359 30.36 21.85 30.66C21.3028 31.2359 20.6423 31.6922 19.91 32C19.0449 32.3282 18.125 32.4877 17.2 32.4701H14.63V18.05H17.2C18.1299 18.0299 19.0536 18.2071 19.91 18.57C20.6427 18.8541 21.3047 19.2943 21.85 19.86C22.0047 20.0272 22.1483 20.2042 22.28 20.39C23.2494 21.8215 23.7428 23.5221 23.69 25.25C23.7204 26.7928 23.3372 28.3155 22.58 29.66ZM29.65 29C29.4274 29.616 29.0879 30.183 28.65 30.67C28.091 31.2478 27.4163 31.701 26.67 32C26.0294 32.2468 25.3551 32.3952 24.67 32.44L24.77 32.3301C26.5048 30.3758 27.4058 27.8202 27.28 25.21C27.3012 23.9879 27.0768 22.7739 26.62 21.64C26.2606 20.6403 25.7105 19.7199 25 18.93C24.6918 18.6124 24.3572 18.3214 24 18.06C24.9093 18.0425 25.8122 18.2162 26.65 18.57C27.3784 18.8567 28.0366 19.2967 28.58 19.86C29.0035 20.3013 29.3424 20.8165 29.58 21.38C30.137 22.5933 30.4171 23.9152 30.4 25.25C30.4166 26.5389 30.1611 27.8167 29.65 29Z" fill="#3333FF"/>
</g>
</svg>'''

details_query = '''
    query getManifestationViaMaterialByFaust($faust: String!) {
  manifestation(faust: $faust) {
    ...ManifestationBasicDetails
  }
}
    
    fragment ManifestationBasicDetails on Manifestation {
  pid
  titles {
    full
  }
  creators {
    display
  }
  cover {
    thumbnail
  }
  materialTypes {
    materialTypeSpecific {
      display
    }
  }
}
'''

status_query = '''
    query BasicUser {
      user {
        name
        mail
        address
        postalCode
        isCPRValidated
        loggedInAgencyId
        loggedInBranchId
        municipalityAgencyId
        agencies {
          id
          name
          type
          hitcount
          user {
            mail
          }
          result {
            branchId
            name
          }
        }
        debt {
            title
            amount
            creator
            date
            currency
            agencyId
        }
        loans {
          agencyId
          loanId
          dueDate
          title
          creator
          manifestation {
            pid
            ...manifestationTitleFragment
            ownerWork {
              workId
            }
            creators {
              ...creatorsFragment
            }
            materialTypes {
              ...materialTypesFragment
            }
            cover {
              thumbnail
            }
            recordCreationDate
          }
        }
        orders {
          orderId
          status
          pickUpBranch {
            agencyName
            agencyId
          }
          pickUpExpiryDate
          holdQueuePosition
          creator
          orderType
          orderDate
          title
          manifestation {
            pid
            ...manifestationTitleFragment
            ownerWork {
              workId
            }
            creators {
              ...creatorsFragment
            }
            materialTypes {
              ...materialTypesFragment
            }
            cover {
              thumbnail
            }
            recordCreationDate
          }
        }   
      }
    }
    fragment creatorsFragment on CreatorInterface {
  ... on Corporation {
    __typename
    display
    nameSort
    roles {
      function {
        plural
        singular
      }
      functionCode
    }
  }
  ... on Person {
    __typename
    display
    nameSort
    roles {
      function {
        plural
        singular
      }
      functionCode
    }
  }
}
    fragment manifestationTitleFragment on Manifestation {
  titles {
    main
    full
  }
}
    fragment materialTypesFragment on MaterialType {
  materialTypeGeneral {
    code
    display
  }
  materialTypeSpecific {
    code
    display
  }
}'''