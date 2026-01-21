{-# LANGUAGE DeriveGeneric #-}
{-# LANGUAGE OverloadedStrings #-}

-------------------------------------------------------------------------------
-- Config Section: fonts, colors, layouts, style options
-------------------------------------------------------------------------------
module Main where

import Data.Aeson (FromJSON(..), defaultOptions, eitherDecode, genericParseJSON, fieldLabelModifier, Value)
import Data.Aeson.Types (Parser)
import Data.ByteString.Lazy qualified as BSL
import Data.Maybe (fromMaybe)
import Data.Text qualified as T
import GHC.Generics (Generic)

-- pdf library import
data FontConfig = FontConfig
  { fontHeader :: String,
    fontBody :: String,
    sizeHeader :: Double,
    sizeBody :: Double
  }
  deriving (Show, Generic)

instance FromJSON FontConfig

-- Only black on white, but allow for possible future extension.
data ColorConfig = ColorConfig
  { fgColor :: String, -- e.g., "#000000"
    bgColor :: String, -- e.g., "#FFFFFF"
    accentColor :: String -- for lines etc.
  }
  deriving (Show, Generic)

instance FromJSON ColorConfig

data LayoutConfig = LayoutConfig
  { marginTop :: Double,
    marginBottom :: Double,
    marginLeft :: Double,
    marginRight :: Double,
    sectionSpacing :: Double
  }
  deriving (Show, Generic)

instance FromJSON LayoutConfig

-- Top-level config embodies all UI-style choices
data CVConfig = CVConfig
  { font :: Maybe FontConfig,
    color :: Maybe ColorConfig,
    layout :: Maybe LayoutConfig
  }
  deriving (Show, Generic)

instance FromJSON CVConfig

defaultFontConfig :: FontConfig
defaultFontConfig = FontConfig "Helvetica-Bold" "Helvetica" 18 11

defaultColorConfig :: ColorConfig
defaultColorConfig = ColorConfig "#000000" "#FFFFFF" "#505050"

defaultLayoutConfig :: LayoutConfig
defaultLayoutConfig = LayoutConfig 36 36 36 36 18 -- points

-------------------------------------------------------------------------------
-- CV Section: data types corresponding to sections in the CV
-------------------------------------------------------------------------------
data Link = Link
  { linkText :: String
  , linkHref :: String
  } deriving (Show, Generic)
instance FromJSON Link where
  parseJSON :: Value -> Parser Link
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of { "linkText" -> "text"; "linkHref" -> "href"; x -> x } }

-- Allow (Maybe) for extra/optional fields in PersonalInfo
-- Added description, title, github, website, upwork as Maybe fields
-- All as per your file structure

data PersonalInfo = PersonalInfo
  { piName        :: String
  , piDescription :: Maybe String
  , piTitle       :: Maybe String
  , piEmail       :: Maybe String
  , piGithub      :: Maybe String
  , piWebsite     :: Maybe String
  , piUpwork      :: Maybe String
  } deriving (Show, Generic)
instance FromJSON PersonalInfo where
  parseJSON :: Value -> Parser PersonalInfo
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "piName" -> "name"
    ; "piDescription" -> "description"
    ; "piTitle" -> "title"
    ; "piEmail" -> "email"
    ; "piGithub" -> "github"
    ; "piWebsite" -> "website"
    ; "piUpwork" -> "upwork"
    ; x -> x }

data Experience = Experience
  { expCompany      :: String
  , expPosition     :: Maybe String
  , expLocation     :: Maybe String
  , expFrom         :: Maybe String
  , expTo           :: Maybe String
  , expDescription  :: Maybe String
  , expLinks        :: Maybe [Link]
  , expFeedback     :: Maybe String
  } deriving (Show, Generic)
instance FromJSON Experience where
  parseJSON :: Value -> Parser Experience
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "expCompany" -> "company"
    ; "expPosition" -> "position"
    ; "expLocation" -> "location"
    ; "expFrom" -> "from"
    ; "expTo" -> "to"
    ; "expDescription" -> "description"
    ; "expLinks" -> "links"
    ; "expFeedback" -> "feedback"
    ; x -> x }

data Education = Education
  { eduInstitution :: String
  , eduLocation    :: Maybe String
  , eduDegree      :: Maybe String
  , eduField       :: Maybe String
  , eduFrom        :: Maybe String
  , eduTo          :: Maybe String
  , eduSummary     :: Maybe String
  } deriving (Show, Generic)
instance FromJSON Education where
  parseJSON :: Value -> Parser Education
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "eduInstitution" -> "institution"
    ; "eduLocation" -> "location"
    ; "eduDegree" -> "degree"
    ; "eduField" -> "field"
    ; "eduFrom" -> "from"
    ; "eduTo" -> "to"
    ; "eduSummary" -> "summary"
    ; x -> x }

data Skill = Skill
  { skillCategory :: String
  , skillItems    :: [String]
  } deriving (Show, Generic)
instance FromJSON Skill where
  parseJSON :: Value -> Parser Skill
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "skillCategory" -> "category"
    ; "skillItems" -> "items"
    ; x -> x }

data Certificate = Certificate
  { certName        :: String
  , certIssuer      :: Maybe String
  , certDescription :: Maybe String
  , certDate        :: Maybe String
  , certLinks       :: Maybe [Link]
  } deriving (Show, Generic)
instance FromJSON Certificate where
  parseJSON :: Value -> Parser Certificate
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "certName" -> "name"
    ; "certIssuer" -> "issuer"
    ; "certDescription" -> "description"
    ; "certDate" -> "date"
    ; "certLinks" -> "links"
    ; x -> x }

-- The main CV type; make most fields optional, and support certificates
-- Allow missing fields to not break parsing (by using Maybe or [])
data CV = CV
  { cvPersonalInfo :: PersonalInfo
  , cvExperience   :: [Experience]
  , cvEducation    :: [Education]
  , cvCertificates :: Maybe [Certificate]
  , cvSkills       :: Maybe [Skill]
  , cvConfig       :: Maybe CVConfig
  } deriving (Show, Generic)
instance FromJSON CV where
  parseJSON :: Value -> Parser CV
  parseJSON = genericParseJSON defaultOptions { fieldLabelModifier = \k -> case k of
    "cvPersonalInfo" -> "personalInfo"
    ; "cvExperience" -> "experience"
    ; "cvEducation" -> "education"
    ; "cvCertificates" -> "certificates"
    ; "cvSkills" -> "skills"
    ; "cvConfig" -> "config"
    ; x -> x }

-------------------------------------------------------------------------------
-- Render Section: rendering logic for transforming CV -> PDF
-------------------------------------------------------------------------------

-- Placeholder for graphics-pdf usage
-- import Graphics.PDF
--
-- renderCV :: CV -> PDF ()
-- renderCV cv = ...

-------------------------------------------------------------------------------
-- Program Section: read JSON, parse and drive the rendering (main IO)
-------------------------------------------------------------------------------

main :: IO ()
main = do
  jsonData <- BSL.readFile "cv.json"
  let parseResult = eitherDecode jsonData :: Either String CV
  case parseResult of
    Left err -> putStrLn $ "Error parsing cv.json: " ++ err
    Right cv -> do
      putStrLn "Successfully parsed cv.json!"
      -- TODO: renderCV cv and save as PDF
      putStrLn "(PDF rendering not yet implemented)"
      return ()

-- Note: Add PDF rendering call when renderCV is ready

-------------------------------------------------------------------------------
-- End cv.hs
-------------------------------------------------------------------------------
